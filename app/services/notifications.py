"""Notification service — centralises in-app rows and best-effort FCM push."""

import json
import logging
from typing import Optional

from firebase_admin import messaging
from sqlmodel import Session, select

from app.core.firebase import is_fcm_available
from app.db.models import FCMToken, Notification, NotificationType

logger = logging.getLogger(__name__)


def create_notification(
    session: Session,
    user_id: int,
    notification_type: NotificationType,
    title: str,
    body: str,
    data: Optional[dict] = None,
    *,
    send_push: bool = True,
    commit: bool = True,
) -> Notification:
    """Create an in-app notification.

    ``commit=False`` lets a caller include the row in a larger business
    transaction. Push delivery is intentionally deferred in that mode: external
    delivery must never happen for a database transaction that might roll back.
    Call :func:`send_notification_push` after the outer commit succeeds.
    """
    notification = Notification(
        user_id=user_id,
        type=notification_type.value,
        title=title,
        body=body,
        data=json.dumps(data) if data else None,
    )
    session.add(notification)

    if commit:
        try:
            session.commit()
            session.refresh(notification)
        except Exception:
            session.rollback()
            raise
        if send_push:
            send_notification_push(session, notification)
    else:
        # Assign primary keys / surface DB constraint errors without ending the
        # caller-owned transaction.
        session.flush()

    return notification


def send_notification_push(session: Session, notification: Notification) -> None:
    """Deliver one already-committed notification through FCM, best-effort."""
    data = None
    if notification.data:
        try:
            data = json.loads(notification.data)
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "Notification id=%s contains invalid JSON data; sending without data",
                notification.id,
            )
    _send_fcm_push(
        session,
        notification.user_id,
        notification.title,
        notification.body,
        data,
    )


def _send_fcm_push(
    session: Session,
    user_id: int,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> None:
    """Best-effort FCM wrapper; delivery failures never fail the API action."""
    try:
        _send_fcm_push_unguarded(session, user_id, title, body, data)
    except Exception as exc:  # noqa: BLE001 — external delivery is best-effort
        logger.warning(
            "FCM push to user_id=%s failed before delivery: %s",
            user_id,
            type(exc).__name__,
        )


def _send_fcm_push_unguarded(
    session: Session,
    user_id: int,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> None:
    if not is_fcm_available():
        logger.debug(
            "Skipping FCM push to user_id=%s — Firebase is not configured", user_id
        )
        return

    logger.info("FCM push → user_id=%s  title=%r", user_id, title)
    tokens = session.exec(select(FCMToken).where(FCMToken.user_id == user_id)).all()
    str_data = {k: str(v) for k, v in data.items()} if data else {}

    for user_token in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=str_data,
                token=user_token.token,
            )
            response = messaging.send(message)
            logger.info("FCM message sent: %s", response)
        except Exception as exc:  # noqa: BLE001 — delivery is best-effort
            logger.warning(
                "FCM delivery failed for token id=%s (user_id=%s): %s",
                user_token.id,
                user_id,
                exc,
            )
