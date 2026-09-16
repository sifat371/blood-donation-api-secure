"""Auth service — JWTs, Google verification, and refresh-token rotation."""

import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from jose import JWTError, jwt
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import AuditLog, RefreshToken, User

logger = logging.getLogger(__name__)


# ── JWT helpers ──────────────────────────────────────────


def create_access_token(user_id: int, extra: Optional[dict] = None) -> str:
    payload = {
        "sub": str(user_id),
        "exp": utc_now() + timedelta(minutes=settings.access_token_expire_minutes),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        if payload.get("type") != "access":
            return None
        return payload
    except JWTError:
        return None


# ── Refresh tokens ───────────────────────────────────────


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_refresh_token(
    session: Session, user_id: int, *, commit: bool = True
) -> str:
    """Generate a refresh token; optionally leave persistence to the caller."""
    raw = secrets.token_urlsafe(64)
    rt = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw),
        expires_at=utc_now() + timedelta(days=settings.refresh_token_expire_days),
    )
    session.add(rt)
    if commit:
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    else:
        session.flush()
    return raw


def rotate_refresh_token(
    session: Session, raw_token: str
) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Consume one refresh token and persist its replacement atomically."""
    token_hash = _hash_token(raw_token)
    rt = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).first()
    if rt is None:
        return None, None, None

    if rt.revoked:
        logger.warning(
            "Refresh-token reuse detected for user_id=%s — revoking all.", rt.user_id
        )
        _revoke_all_for_user(session, rt.user_id, commit=False)
        _audit(
            session,
            rt.user_id,
            "refresh_token_reuse_detected",
            commit=False,
        )
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        return None, None, None

    if rt.expires_at < utc_now():
        rt.revoked = True
        session.add(rt)
        session.commit()
        return None, None, None

    # Validate account state before issuing a replacement. This prevents an
    # orphan token from being created for a deleted or subsequently-unverified
    # account.
    user = session.get(User, rt.user_id)
    if user is None or not user.email_verified:
        _revoke_all_for_user(session, rt.user_id, commit=False)
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
        return None, None, None

    try:
        rt.revoked = True
        session.add(rt)
        new_refresh = create_refresh_token(session, rt.user_id, commit=False)
        new_access = create_access_token(rt.user_id)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return new_access, new_refresh, rt.user_id


def revoke_refresh_token(session: Session, raw_token: str) -> bool:
    rt = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw_token))
    ).first()
    if rt is None:
        return False
    rt.revoked = True
    session.add(rt)
    session.commit()
    return True


def _revoke_all_for_user(
    session: Session, user_id: int, *, commit: bool = True
) -> None:
    tokens = session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked == False,  # noqa: E712
        )
    ).all()
    for token in tokens:
        token.revoked = True
        session.add(token)
    if commit:
        session.commit()
    else:
        session.flush()


# ── Google ID-token verification ─────────────────────────


async def verify_google_id_token(id_token: str) -> Optional[dict]:
    audience = settings.google_client_id.strip()
    if not audience:
        logger.error("Google ID-token verification attempted without GOOGLE_CLIENT_ID")
        return None

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        return google_id_token.verify_oauth2_token(
            id_token,
            google_requests.Request(),
            audience,
        )
    except Exception as exc:
        logger.warning("Google ID token verification failed: %s", exc)
        return None


# ── Audit helpers ─────────────────────────────────────────


def _audit(
    session: Session,
    user_id: Optional[int],
    action: str,
    entity: str = "auth",
    entity_id: Optional[str] = None,
    *,
    commit: bool = True,
) -> None:
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
    )
    session.add(log)
    if commit:
        session.commit()
    else:
        session.flush()


def audit_log(
    session: Session,
    user_id: Optional[int],
    action: str,
    entity: str,
    entity_id: Optional[str] = None,
    metadata: Optional[str] = None,
    *,
    commit: bool = True,
) -> None:
    """Create an audit row; ``commit=False`` joins a caller-owned transaction."""
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        metadata_json=metadata,
    )
    session.add(log)
    if commit:
        session.commit()
    else:
        session.flush()
