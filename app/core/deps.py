"""
FastAPI dependency injection — auth, database session.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select

from app.db.database import get_session
from app.db.models import User
from app.services.auth_service import decode_access_token

security_scheme = HTTPBearer()


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials, Depends(security_scheme)
    ],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    """
    Dependency that extracts and validates the Bearer token,
    then returns the corresponding User row.
    """
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "SESSION_EXPIRED",
                "message": "Your session has expired. Please sign in again.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = int(payload["sub"])
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "SESSION_EXPIRED",
                "message": "This account no longer exists.",
            },
        )
    if not user.email_verified:
        # Defence in depth. /auth/login already refuses to issue a session to an
        # unverified account, so reaching here means a token was obtained some
        # other way; every authenticated route should still say no.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "EMAIL_NOT_VERIFIED",
                "message": "Please verify your email before continuing.",
            },
        )
    return user


# Convenience type aliases
CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_session)]
