"""
Auth service — JWT creation/verification, Google ID token verification,
refresh-token rotation with reuse detection.
"""

import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from jose import JWTError, jwt
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import User, RefreshToken, AuditLog

logger = logging.getLogger(__name__)

# ── JWT helpers ──────────────────────────────────────────


def create_access_token(user_id: int, extra: Optional[dict] = None) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow()
        + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


# ── Refresh tokens ───────────────────────────────────────


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_refresh_token(session: Session, user_id: int) -> str:
    """Generate a random refresh token, store its hash, return the raw value."""
    raw = secrets.token_urlsafe(64)
    token_hash = _hash_token(raw)
    rt = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.utcnow()
        + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(rt)
    session.commit()
    return raw


def rotate_refresh_token(
    session: Session, raw_token: str
) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """
    Rotate: consume the old refresh token and issue a new pair.

    Returns (new_access, new_refresh, user_id) or (None, None, None) on failure.
    Implements **reuse detection**: if a revoked token is presented, revoke
    ALL tokens for that user (potential token theft).
    """
    token_hash = _hash_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    rt = session.exec(stmt).first()

    if rt is None:
        return None, None, None

    # Reuse detection
    if rt.revoked:
        logger.warning(
            "Refresh-token reuse detected for user_id=%s — revoking all.", rt.user_id
        )
        _revoke_all_for_user(session, rt.user_id)
        _audit(session, rt.user_id, "refresh_token_reuse_detected")
        return None, None, None

    if rt.expires_at < datetime.utcnow():
        rt.revoked = True
        session.commit()
        return None, None, None

    # Revoke old
    rt.revoked = True
    session.add(rt)
    session.commit()

    # Issue new pair
    new_access = create_access_token(rt.user_id)
    new_refresh = create_refresh_token(session, rt.user_id)
    return new_access, new_refresh, rt.user_id


def revoke_refresh_token(session: Session, raw_token: str) -> bool:
    token_hash = _hash_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    rt = session.exec(stmt).first()
    if rt is None:
        return False
    rt.revoked = True
    session.add(rt)
    session.commit()
    return True


def _revoke_all_for_user(session: Session, user_id: int) -> None:
    stmt = select(RefreshToken).where(
        RefreshToken.user_id == user_id, RefreshToken.revoked == False
    )
    tokens = session.exec(stmt).all()
    for t in tokens:
        t.revoked = True
        session.add(t)
    session.commit()


# ── Google ID-token verification ─────────────────────────


async def verify_google_id_token(id_token: str) -> Optional[dict]:
    """
    Verify a Google ID token and return the payload
    (sub, email, name, picture, etc.).

    Returns None if verification fails.
    """
    audience = settings.google_client_id.strip()
    if not audience:
        logger.error("Google ID-token verification attempted without GOOGLE_CLIENT_ID")
        return None

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        payload = google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            audience,
        )
        return payload
    except Exception as exc:
        logger.warning("Google ID token verification failed: %s", exc)
        return None


# ── Audit helper ─────────────────────────────────────────


def _audit(
    session: Session,
    user_id: Optional[int],
    action: str,
    entity: str = "auth",
    entity_id: Optional[str] = None,
) -> None:
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
    )
    session.add(log)
    session.commit()


def audit_log(
    session: Session,
    user_id: Optional[int],
    action: str,
    entity: str,
    entity_id: Optional[str] = None,
    metadata: Optional[str] = None,
) -> None:
    """Public audit-log helper used by API routes."""
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        metadata_json=metadata,
    )
    session.add(log)
    session.commit()
