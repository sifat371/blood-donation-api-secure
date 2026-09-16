"""
Token lifetimes and password hashing.

Passwords use bcrypt directly rather than through passlib: passlib 1.7.4 is
unmaintained and its bcrypt backend-detection probe raises against bcrypt 5.x
(it deliberately hashes an over-length secret, which bcrypt 5 now rejects).
bcrypt's own API is small enough that the indirection bought nothing.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

import bcrypt

from app.core.config import settings

ACCESS_TOKEN_EXPIRE = timedelta(
    minutes=settings.access_token_expire_minutes
)
SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm

# bcrypt hashes at most 72 bytes of input and silently ignores the rest, so the
# limit is enforced at validation time instead of letting two different long
# passwords collide.
MAX_PASSWORD_BYTES = 72

BCRYPT_ROUNDS = 12


def password_length_error(password: str) -> str | None:
    """Return a human-readable reason the password is unusable, or None."""
    encoded = password.encode("utf-8")
    if len(encoded) < settings.password_min_length:
        return (
            f"Password must be at least {settings.password_min_length} "
            "characters long."
        )
    if len(encoded) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes long."
    return None


def hash_password(password: str) -> str:
    """Hash a password with bcrypt. The salt is generated per call."""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def verify_password(password: str, hashed: str | None) -> bool:
    """
    Check a password against a stored bcrypt hash.

    A user with no password (Google-only account) must never match, and a
    malformed stored hash must not raise.
    """
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def dummy_password_verify() -> None:
    """
    Burn roughly one bcrypt verification's worth of time.

    Used on the "no such account" login path so that a missing email and a wrong
    password take comparable time and the endpoint does not become an account
    enumeration oracle.
    """
    bcrypt.checkpw(
        b"timing-equalisation",
        b"$2b$12$C6UzMDM.H6dfI/f/IKcEe.7Q1TQ9lLZ8xJd0dK1jkVeK2WnrMkQ0O",
    )


# ── Short-lived verification codes ───────────────────────


def generate_numeric_code(digits: int = 6) -> str:
    """A cryptographically secure zero-padded numeric code."""
    upper = 10**digits
    return f"{secrets.randbelow(upper):0{digits}d}"


def hash_verification_code(code: str) -> str:
    """
    Hash a verification code for storage.

    HMAC-SHA256 keyed with the app secret rather than a bare digest: the code
    space is only a million values, so an unkeyed hash of a leaked database
    column would be trivially reversible with a rainbow table.
    """
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def codes_match(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_verification_code(code), stored_hash)
