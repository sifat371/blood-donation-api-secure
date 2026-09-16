"""
Outbound mail.

Two backends behind one function:

  * **smtp** — a real server, configured entirely from the backend environment.
    Credentials live in `backend/.env`, never in the mobile app, because
    anything in an `EXPO_PUBLIC_*` variable ships inside the APK.
  * **outbox** — writes the message to `backend/dev_outbox/` and logs that it did
    so. For a development machine with no mail server: the verification code is
    still generated, stored hashed, and checked by the backend, so the flow being
    exercised is the real one. The code is readable only by whoever can read the
    server's own filesystem — it is never returned through the API.

`MAIL_BACKEND=auto` (the default) picks smtp when `SMTP_HOST` is set and outbox
otherwise. Set `MAIL_BACKEND=smtp` in production so a missing SMTP_HOST raises
instead of quietly writing verification codes to disk.
"""

import logging
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]


class MailNotConfiguredError(RuntimeError):
    """Raised when the selected backend cannot send."""


def _resolved_backend() -> str:
    configured = (settings.mail_backend or "auto").strip().lower()
    if configured == "auto":
        return "smtp" if settings.smtp_host.strip() else "outbox"
    return configured


def _sender() -> str:
    return (
        settings.mail_from.strip()
        or settings.smtp_user.strip()
        or "no-reply@blood-donation.local"
    )


def _build_message(to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = f"{settings.mail_from_name} <{_sender()}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message


def _safe_filename(email: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._@-]", "_", email)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S-%f")
    return f"{stamp}_{stem}.eml"


def _send_via_outbox(message: EmailMessage, to: str) -> None:
    outbox = BACKEND_DIR / settings.mail_outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / _safe_filename(to)
    path.write_text(message.as_string(), encoding="utf-8")
    # The path, not the contents: the log should not become a second copy of
    # every verification code.
    logger.warning(
        "Mail written to local outbox (NOT delivered): %s  — "
        "Set SMTP_HOST, SMTP_USER, and SMTP_PASSWORD in .env to send real emails.",
        path,
    )


def _send_via_smtp(message: EmailMessage) -> None:
    host = settings.smtp_host.strip()
    if not host:
        raise MailNotConfiguredError(
            "MAIL_BACKEND=smtp but SMTP_HOST is empty."
        )

    with smtplib.SMTP(host, settings.smtp_port, timeout=20) as client:
        if settings.smtp_starttls:
            client.starttls()
        if settings.smtp_user:
            client.login(settings.smtp_user, settings.smtp_password)
        client.send_message(message)
    logger.info("Mail sent via SMTP to %s", message["To"])


def send_mail(to: str, subject: str, body: str) -> str:
    """
    Deliver a message. Returns the backend name that handled it.

    Raises on failure so the caller can decide whether the surrounding operation
    should still be reported as successful.
    """
    backend = _resolved_backend()
    message = _build_message(to, subject, body)

    if backend == "smtp":
        _send_via_smtp(message)
    elif backend == "outbox":
        _send_via_outbox(message, to)
    else:
        raise MailNotConfiguredError(f"Unknown MAIL_BACKEND '{backend}'.")

    return backend
