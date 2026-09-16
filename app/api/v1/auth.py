"""
Auth endpoints (§2.2)

POST /auth/signup               — Email/password registration (starts unverified)
POST /auth/verify-email         — Redeem a verification code, activating the account
POST /auth/resend-verification  — Reissue a code, rate limited
POST /auth/login                — Email/password login (requires a verified email)
POST /auth/google               — Google ID-token login / registration
POST /auth/refresh              — Rotate refresh token
POST /auth/logout               — Revoke refresh token
POST /auth/dev-login            — Dev-only mock login (disabled in production)

Failures carry a machine-readable code alongside the human message:

    {"detail": {"code": "EMAIL_NOT_VERIFIED", "message": "..."}}

so the app can tell "the server said no" apart from "the server was not
reachable" without pattern-matching on prose. Codes never include internal
addresses, stack traces, or token material.
"""

import logging
from fastapi import APIRouter, HTTPException, status, Depends, Request
from sqlmodel import Session, select
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

from app.core.config import settings
from app.core.time import utc_now
from app.core.deps import DbSession
from app.core.security import (
    dummy_password_verify,
    hash_password,
    password_length_error,
    verify_password,
)
from app.db.models import AuthProvider, User
from app.schemas.auth import (
    DevLoginRequest,
    EmailLoginRequest,
    GoogleAuthRequest,
    RefreshRequest,
    ResendVerificationRequest,
    ResendVerificationResponse,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from app.services.auth_service import (
    create_access_token,
    create_refresh_token,
    rotate_refresh_token,
    revoke_refresh_token,
    verify_google_id_token,
    audit_log,
)
from app.services.email_verification_service import (
    ResendResult,
    VerificationResult,
    issue_code,
    resend_code,
    verify_code,
)
from app.services.mail_service import MailNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── Helpers ──────────────────────────────────────────────


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    """A failure the client can branch on without parsing English."""
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def _find_by_email(session: Session, email: str) -> User | None:
    return session.exec(select(User).where(User.email == email)).first()


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _profile_incomplete(user: User) -> bool:
    return not user.blood_group or not user.phone


def _issue_session(session: Session, user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(session, user.id),
        profile_incomplete=_profile_incomplete(user),
    )


# ── Email/password signup ────────────────────────────────


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
def signup(request: Request, body: SignupRequest, session: DbSession):
    """
    Create an account in an unverified state and email a verification code.

    No tokens are returned: the account is not usable until the address is
    confirmed, so issuing a session here would make the check decorative.
    """
    email = _normalise_email(body.email)

    problem = password_length_error(body.password)
    if problem:
        raise api_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", problem
        )

    existing = _find_by_email(session, email)
    if existing is not None:
        # Never overwrite an existing identity or password.
        if existing.google_id and not existing.hashed_password:
            raise api_error(
                status.HTTP_409_CONFLICT,
                "ACCOUNT_EXISTS_GOOGLE",
                "This email is already registered with Google Sign-In. "
                "Use Continue with Google.",
            )
        raise api_error(
            status.HTTP_409_CONFLICT,
            "EMAIL_ALREADY_REGISTERED",
            "An account with this email already exists. Sign in instead.",
        )

    user = User(
        name=body.name.strip(),
        email=email,
        hashed_password=hash_password(body.password),
        phone=(body.phone or "").strip() or None,
        blood_group=(body.blood_group or "").strip() or None,
        gender=(body.gender or "").strip() or None,
        email_verified=False,
        auth_provider=AuthProvider.PASSWORD.value,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    try:
        delivery = issue_code(session, user)
    except (MailNotConfiguredError, OSError) as exc:
        # Undo the half-made account rather than leaving the address locked to a
        # registration that can never be completed.
        logger.error("Verification mail failed for a new signup: %s", exc)
        session.delete(user)
        session.commit()
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "MAIL_DELIVERY_FAILED",
            "Could not send the verification email. Please try again shortly.",
        )

    audit_log(session, user.id, "user_registered", "user", str(user.id))
    audit_log(session, user.id, "verification_code_sent", "user", str(user.id))

    return SignupResponse(
        email=user.email,
        email_verified=False,
        message=(
            "Please verify your email before continuing. We sent a "
            f"{settings.email_verification_code_digits}-digit code to "
            f"{user.email}."
        ),
        delivery=delivery,
    )


# ── Email verification ───────────────────────────────────


@router.post("/verify-email", response_model=VerifyEmailResponse)
@limiter.limit("10/minute")
def verify_email(request: Request, body: VerifyEmailRequest, session: DbSession):
    """Redeem a verification code. The decision is entirely server-side."""
    email = _normalise_email(body.email)
    user = _find_by_email(session, email)
    if user is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "ACCOUNT_NOT_FOUND",
            "No account exists for that email address.",
        )

    result = verify_code(session, user, body.code.strip())

    if result is VerificationResult.OK:
        audit_log(session, user.id, "email_verified", "user", str(user.id))
        return VerifyEmailResponse(
            email=user.email,
            email_verified=True,
            message="Email verified. You can sign in now.",
        )

    if result is VerificationResult.ALREADY_VERIFIED:
        return VerifyEmailResponse(
            email=user.email,
            email_verified=True,
            message="This email is already verified. You can sign in.",
        )

    if result is VerificationResult.EXPIRED:
        raise api_error(
            status.HTTP_400_BAD_REQUEST,
            "VERIFICATION_CODE_EXPIRED",
            "That code has expired. Request a new one.",
        )

    if result is VerificationResult.TOO_MANY_ATTEMPTS:
        raise api_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "VERIFICATION_ATTEMPTS_EXCEEDED",
            "Too many incorrect attempts. Request a new code.",
        )

    raise api_error(
        status.HTTP_400_BAD_REQUEST,
        "VERIFICATION_CODE_INVALID",
        "That code is not correct.",
    )


@router.post("/resend-verification", response_model=ResendVerificationResponse)
@limiter.limit("3/minute")
def resend_verification(
    request: Request, body: ResendVerificationRequest, session: DbSession
):
    """
    Reissue a verification code.

    Two limits, because one is not enough: slowapi caps requests per client
    address, and a per-account cooldown stops the same mailbox being flooded from
    several addresses.
    """
    email = _normalise_email(body.email)
    user = _find_by_email(session, email)
    if user is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "ACCOUNT_NOT_FOUND",
            "No account exists for that email address.",
        )

    try:
        result = resend_code(session, user)
    except (MailNotConfiguredError, OSError) as exc:
        logger.error("Verification mail failed on resend: %s", exc)
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "MAIL_DELIVERY_FAILED",
            "Could not send the verification email. Please try again shortly.",
        )

    if result is ResendResult.ALREADY_VERIFIED:
        return ResendVerificationResponse(
            message="This email is already verified. You can sign in."
        )

    if result is ResendResult.COOLDOWN:
        raise api_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "RESEND_COOLDOWN",
            "A code was just sent. Wait "
            f"{settings.email_verification_resend_cooldown_seconds} seconds "
            "before requesting another.",
        )

    audit_log(session, user.id, "verification_code_sent", "user", str(user.id))
    return ResendVerificationResponse(
        message=f"A new code is on its way to {user.email}.",
        delivery=None,
    )


# ── Email/password login ─────────────────────────────────


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: EmailLoginRequest, session: DbSession):
    """
    Sign in with email and password.

    All three conditions must hold: the account exists, the password matches,
    and the address has been verified. An unverified account gets no session at
    all — only a 403 telling the app to go and verify.
    """
    email = _normalise_email(body.email)
    user = _find_by_email(session, email)

    if user is None:
        # Spend comparable time to a real check so the endpoint does not become
        # a fast/slow oracle for which addresses exist.
        dummy_password_verify()
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_CREDENTIALS",
            "Incorrect email or password.",
        )

    if not user.hashed_password:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "PASSWORD_NOT_SET",
            "This account signs in with Google. Use Continue with Google.",
        )

    if not verify_password(body.password, user.hashed_password):
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_CREDENTIALS",
            "Incorrect email or password.",
        )

    if not user.email_verified:
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "EMAIL_NOT_VERIFIED",
            "Please verify your email before signing in.",
        )

    audit_log(session, user.id, "user_login", "user", str(user.id))
    return _issue_session(session, user)


# ── Google login ─────────────────────────────────────────


@router.post("/google", response_model=TokenResponse)
@limiter.limit("5/minute")
async def google_login(request: Request, body: GoogleAuthRequest, session: DbSession):
    """
    Verify a Google ID token. If the email already exists, issue tokens.
    Otherwise create a new user and flag `profile_incomplete=True`.
    """
    if not settings.google_client_id.strip():
        raise api_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GOOGLE_AUTH_NOT_CONFIGURED",
            "Google sign-in is not configured on this server.",
        )

    payload = await verify_google_id_token(body.id_token)
    if payload is None:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "GOOGLE_AUTH_FAILED",
            "Google sign-in could not be verified. Please try again.",
        )

    email = payload.get("email")
    if not email:
        raise api_error(
            status.HTTP_400_BAD_REQUEST,
            "GOOGLE_AUTH_FAILED",
            "Google did not return an email address for this account.",
        )
    email = _normalise_email(email)

    # Google states explicitly whether it has verified the address. Absent claim
    # is treated as verified: every Google account reaching this point has been
    # through Google's own sign-in.
    google_says_verified = payload.get("email_verified", True) is not False
    google_sub = payload.get("sub")

    user = _find_by_email(session, email)
    profile_incomplete = False

    if user is None:
        user = User(
            name=payload.get("name", ""),
            email=email,
            google_id=google_sub,
            profile_photo=payload.get("picture"),
            email_verified=google_says_verified,
            email_verified_at=utc_now() if google_says_verified else None,
            auth_provider=AuthProvider.GOOGLE.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        profile_incomplete = True
        audit_log(session, user.id, "user_registered", "user", str(user.id))
    else:
        # Link the Google identity to an account that was created with a
        # password, without touching the password itself. An account that
        # already carries a *different* google_id is left alone — that would be
        # two Google identities claiming one address, which should not silently
        # overwrite either.
        changed = False
        if google_sub and user.google_id is None:
            user.google_id = google_sub
            changed = True
            audit_log(
                session, user.id, "google_identity_linked", "user", str(user.id)
            )
        if google_says_verified and not user.email_verified:
            user.email_verified = True
            user.email_verified_at = utc_now()
            changed = True
        if not user.profile_photo and payload.get("picture"):
            user.profile_photo = payload.get("picture")
            changed = True

        expected_provider = (
            AuthProvider.BOTH.value
            if user.google_id and user.hashed_password
            else AuthProvider.GOOGLE.value
            if user.google_id
            else AuthProvider.PASSWORD.value
        )
        if user.auth_provider != expected_provider:
            user.auth_provider = expected_provider
            changed = True

        if changed:
            user.updated_at = utc_now()
            session.add(user)
            session.commit()
            session.refresh(user)

        audit_log(session, user.id, "user_login", "user", str(user.id))

    if _profile_incomplete(user):
        profile_incomplete = True

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(session, user.id),
        profile_incomplete=profile_incomplete,
    )


# ── Refresh ──────────────────────────────────────────────


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("10/minute")
def refresh(request: Request, body: RefreshRequest, session: DbSession):
    """Rotate the refresh token and issue a new access + refresh pair."""
    new_access, new_refresh, user_id = rotate_refresh_token(
        session, body.refresh_token
    )
    if new_access is None:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "SESSION_EXPIRED",
            "Your session has expired. Please sign in again.",
        )

    user = session.get(User, user_id)
    if user is None:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "SESSION_EXPIRED",
            "Your session has expired. Please sign in again.",
        )

    # Defence in depth, matching get_current_user: a session belonging to an
    # unverified account should not be extendable. /auth/login never issues one,
    # so this is unreachable by design — but refresh is the one call that can keep
    # a session alive indefinitely, so it verifies rather than assumes.
    if not user.email_verified:
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "EMAIL_NOT_VERIFIED",
            "Please verify your email before continuing.",
        )

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        # Reported from the current row rather than left at the default. The app
        # decides between the main screen and the complete-profile screen from
        # this flag, and a session restored by refresh alone would otherwise be
        # told an unfinished profile was finished.
        profile_incomplete=_profile_incomplete(user),
    )


# ── Logout ───────────────────────────────────────────────


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest, session: DbSession):
    """Revoke the provided refresh token."""
    revoke_refresh_token(session, body.refresh_token)
    return None


# ── Dev-only mock login ──────────────────────────────────


@router.post("/dev-login", response_model=TokenResponse)
@limiter.limit("20/minute")
def dev_login(request: Request, body: DevLoginRequest, session: DbSession):
    """
    Development-only endpoint: login or create a user by email
    without Google OAuth. **Disabled when ENVIRONMENT=production**.
    """
    if settings.environment == "production":
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "ENDPOINT_DISABLED",
            "Dev login is disabled in production.",
        )

    email = _normalise_email(body.email)
    user = _find_by_email(session, email)
    profile_incomplete = False

    if user is None:
        # Created verified on purpose: this endpoint exists so the automated
        # tests and LAN checks can obtain a session without a mailbox. It is
        # unreachable in production, so it cannot be used to skip verification
        # for a real account.
        user = User(
            name=body.name,
            email=email,
            email_verified=True,
            email_verified_at=utc_now(),
            auth_provider=AuthProvider.GOOGLE.value,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        profile_incomplete = True
    elif _profile_incomplete(user):
        profile_incomplete = True

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(session, user.id),
        profile_incomplete=profile_incomplete,
    )
