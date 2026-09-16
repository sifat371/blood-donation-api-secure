"""
Blood Donation API — FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings, validate_runtime_settings
from app.core.firebase import init_firebase, is_fcm_available
from app.db.database import create_db_and_tables
from app.db.migrations import run_migrations


# Import all model classes so SQLModel sees them at table-creation time
from app.db.models import (  # noqa: F401
    User,
    BloodRequest,
    DonationHistory,
    Notification,
    RefreshToken,
    EmailVerification,
    AuditLog,
    UserLocation,
    ConversationHistory,
    FCMToken,
)

from app.api.v1.auth import router as auth_router
from app.api.v1.profile import router as profile_router
from app.api.v1.donors import router as donors_router
from app.api.v1.blood_requests import router as blood_requests_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.donation_history import router as donation_history_router
from app.api.v1.chat import router as chat_router


# ── Rate limiter ─────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

# ── Lifespan ─────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_settings()
    create_db_and_tables()
    # create_all adds missing tables but never missing columns, so a database
    # created by an earlier version needs the additive migrations too.
    run_migrations()
    # Best-effort: a missing/invalid service account disables push but must
    # never stop the API from starting.
    init_firebase()
    yield

# ── App ──────────────────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="REST API for the Blood Donation mobile application",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ─────────────────────────────────────────────────
#
# Driven entirely by the CORS_ORIGINS env var (comma-separated). Note that
# CORS only affects browser clients (the Expo web build, Swagger UI); native
# iOS/Android builds are unaffected either way.
#
# "*" and allow_credentials=True is an invalid combination that browsers
# reject outright, so credentials are only enabled for an explicit allow-list.
# This API authenticates with Bearer tokens rather than cookies, so disabling
# credentials for the wildcard case costs nothing.

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"]
allow_all_origins = "*" in origins

if allow_all_origins and settings.environment == "production":
    logging.getLogger(__name__).warning(
        "CORS_ORIGINS is '*' while ENVIRONMENT=production. Set CORS_ORIGINS to "
        "an explicit comma-separated list of your web origins."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────

app.include_router(auth_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(donors_router, prefix="/api/v1")
app.include_router(blood_requests_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(donation_history_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")


@app.get("/")
def root():
    return {"message": "Blood Donation API is running", "version": "1.0.0"}


@app.get("/health")
def health():
    """
    Liveness + capability probe.

    Handy when setting up device testing: open http://<LAN-IP>:8000/health in
    the phone's browser to confirm the phone can actually reach the backend.
    """
    return {
        "status": "healthy",
        "environment": settings.environment,
        "push_notifications": is_fcm_available(),
    }
