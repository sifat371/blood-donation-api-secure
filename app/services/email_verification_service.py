"""
Email verification.

The whole decision lives here, on the server. A code is:

  * generated with `secrets`, never derived from anything guessable;
  * stored only as an HMAC-SHA256 keyed with the app secret;
  * bound to the account *and* the address it was issued for;
  * valid for a bounded time (EMAIL_VERIFICATION_TTL_MINUTES);
  * single-use — consuming it marks it consumed and invalidates every other
    outstanding code for that account;
  * limited to EMAIL_VERIFICATION_MAX_ATTEMPTS wrong guesses before it is burned.

Nothing in this module ever returns the plaintext code to an API caller. The
frontend cannot mark an account verified; it can only submit a code and be told
whether the server accepted it.
"""

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Tuple

from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utc_now
from app.core.security import (
    codes_match,
    generate_numeric_code,
    hash_verification_code,
)
from app.db.models import AuthProvider, EmailVerification, User
from app.services.mail_service import send_mail

logger = logging.getLogger(__name__)


class VerificationResult(str, Enum):
    OK = "ok"
    INVALID = "invalid"
    EXPIRED = "expired"
    ALREADY_VERIFIED = "already_verified"
    TOO_MANY_ATTEMPTS = "too_many_attempts"


class ResendResult(str, Enum):
    SENT = "sent"
    ALREADY_VERIFIED = "already_verified"
    COOLDOWN = "cooldown"


def _now() -> datetime:
    return utc_now()


def _invalidate_outstanding(session: Session, user_id: int) -> None:
    """Burn every live challenge for an account."""
    stmt = select(EmailVerification).where(
        EmailVerification.user_id == user_id,
        EmailVerification.consumed == False,  # noqa: E712 — SQL, not Python
    )
    for row in session.exec(stmt).all():
        row.consumed = True
        row.consumed_at = _now()
        session.add(row)


def _latest_challenge(
    session: Session, user_id: int
) -> Optional[EmailVerification]:
    stmt = (
        select(EmailVerification)
        .where(EmailVerification.user_id == user_id)
        .order_by(EmailVerification.id.desc())
    )
    return session.exec(stmt).first()


def _compose(user: User, code: str) -> Tuple[str, str]:
    minutes = settings.email_verification_ttl_minutes
    subject = "Verify your Blood Donation account"
    body = (
        f"Hello {user.name or 'there'},\n\n"
        "Use this code to verify your email address and activate your "
        "Blood Donation account:\n\n"
        f"    {code}\n\n"
        f"The code expires in {minutes} minutes and can be used once.\n"
        "If you did not create this account, you can ignore this message.\n"
    )
    return subject, body


def issue_code(session: Session, user: User) -> str:
    """
    Create and email a fresh verification code, invalidating any earlier one.

    Returns the mail backend that accepted the message. The code itself is
    deliberately not returned — the caller must not be able to leak it.
    """
    _invalidate_outstanding(session, user.id)

    code = generate_numeric_code(settings.email_verification_code_digits)
    challenge = EmailVerification(
        user_id=user.id,
        email=user.email,
        code_hash=hash_verification_code(code),
        expires_at=_now()
        + timedelta(minutes=settings.email_verification_ttl_minutes),
    )
    session.add(challenge)
    session.commit()

    backend = send_mail(user.email, *_compose(user, code))
    logger.info(
        "Verification code issued for user_id=%s via %s", user.id, backend
    )
    return backend


def can_resend(session: Session, user: User) -> bool:
    """False while the per-account cooldown is still running."""
    latest = _latest_challenge(session, user.id)
    if latest is None:
        return True
    elapsed = (_now() - latest.created_at).total_seconds()
    return elapsed >= settings.email_verification_resend_cooldown_seconds


def resend_code(session: Session, user: User) -> ResendResult:
    if user.email_verified:
        return ResendResult.ALREADY_VERIFIED
    if not can_resend(session, user):
        return ResendResult.COOLDOWN
    issue_code(session, user)
    return ResendResult.SENT


def verify_code(
    session: Session, user: User, code: str
) -> VerificationResult:
    """
    Check a submitted code and, on success, activate the account.

    Every failure path still records the attempt, so a six-digit code cannot be
    ground down by repeated guessing.
    """
    if user.email_verified:
        return VerificationResult.ALREADY_VERIFIED

    stmt = (
        select(EmailVerification)
        .where(
            EmailVerification.user_id == user.id,
            EmailVerification.email == user.email,
            EmailVerification.consumed == False,  # noqa: E712
        )
        .order_by(EmailVerification.id.desc())
    )
    challenge = session.exec(stmt).first()

    if challenge is None:
        return VerificationResult.INVALID

    if challenge.expires_at < _now():
        challenge.consumed = True
        challenge.consumed_at = _now()
        session.add(challenge)
        session.commit()
        return VerificationResult.EXPIRED

    if challenge.attempts >= settings.email_verification_max_attempts:
        challenge.consumed = True
        challenge.consumed_at = _now()
        session.add(challenge)
        session.commit()
        return VerificationResult.TOO_MANY_ATTEMPTS

    if not codes_match(code, challenge.code_hash):
        challenge.attempts += 1
        exhausted = (
            challenge.attempts >= settings.email_verification_max_attempts
        )
        if exhausted:
            challenge.consumed = True
            challenge.consumed_at = _now()
        session.add(challenge)
        session.commit()
        return (
            VerificationResult.TOO_MANY_ATTEMPTS
            if exhausted
            else VerificationResult.INVALID
        )

    # Success: consume the code and activate the account in one transaction.
    challenge.consumed = True
    challenge.consumed_at = _now()
    session.add(challenge)

    user.email_verified = True
    user.email_verified_at = _now()
    user.updated_at = _now()
    if user.hashed_password and user.google_id:
        user.auth_provider = AuthProvider.BOTH.value
    elif user.hashed_password:
        user.auth_provider = AuthProvider.PASSWORD.value
    session.add(user)
    session.commit()

    return VerificationResult.OK
