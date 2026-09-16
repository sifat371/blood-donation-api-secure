"""
Firebase Admin SDK initialisation.

Push notifications are an *optional* capability: the API must start and serve
every endpoint even when Firebase credentials are absent or invalid (CI, a
fresh clone, a teammate's machine). So initialisation is best-effort and the
result is exposed through `is_fcm_available()` for the notification service to
check before attempting a send.

Credential resolution order:
  1. settings.fcm_credentials_path (absolute, or relative to backend/)
  2. app/serviceAccount.json
  3. GOOGLE_APPLICATION_CREDENTIALS via application-default credentials
"""

import logging
from pathlib import Path
from typing import Optional

import firebase_admin
from firebase_admin import credentials

from app.core.config import settings

logger = logging.getLogger(__name__)

# backend/app/core/firebase.py → backend/app → backend/
APP_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = APP_DIR.parent

_initialised = False


def _resolve_credentials_path() -> Optional[Path]:
    """Return the first credentials file that actually exists, or None."""
    candidates = []
    if settings.fcm_credentials_path:
        configured = Path(settings.fcm_credentials_path)
        candidates.append(
            configured if configured.is_absolute() else BACKEND_DIR / configured
        )
    candidates.append(APP_DIR / "serviceAccount.json")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def init_firebase() -> bool:
    """
    Initialise the Firebase Admin app if it isn't already.

    Never raises: returns True when FCM is usable, False otherwise.
    """
    global _initialised

    if firebase_admin._apps:
        _initialised = True
        return True

    cred_path = _resolve_credentials_path()
    try:
        if cred_path is not None:
            firebase_admin.initialize_app(credentials.Certificate(str(cred_path)))
        else:
            # Falls back to GOOGLE_APPLICATION_CREDENTIALS / metadata server.
            firebase_admin.initialize_app()
        _initialised = True
        logger.info("Firebase Admin initialised — push notifications enabled")
    except Exception as exc:  # noqa: BLE001 — startup must not fail on this
        _initialised = False
        logger.warning(
            "Firebase Admin not initialised (%s). Push notifications are "
            "disabled; in-app notifications still work normally.",
            type(exc).__name__,
        )
    return _initialised


def is_fcm_available() -> bool:
    """True when a Firebase app is initialised and pushes can be attempted."""
    return _initialised and bool(firebase_admin._apps)
