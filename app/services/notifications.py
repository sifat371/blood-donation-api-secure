"""Notification persistence and durable per-device delivery creation."""

import json
from typing import Optional

from firebase_admin import messaging
from sqlmodel import Session, select

from app.core.firebase import is_fcm_available
from app.db.models import FCMToken, Notification, NotificationDelivery, NotificationType


def create_notification(
    session: Session,
    user_id: int,
    notification_type: NotificationType | str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    *,
    dedupe_key: Optional[str] = None,
    commit: bool = True,
    send_push: Optional[bool] = None,
) -> Notification:
    """Persist an inbox notification and snapshot active-device delivery jobs.

    The function performs database work only. ``commit=False`` joins a caller-owned
    business transaction, so inbox and delivery rows roll back with the business
    change. ``send_push`` is retained temporarily as an inert P2 compatibility
    argument; network delivery is exclusively worker-owned in P3.1.
    """
    if dedupe_key:
        existing = session.exec(
            select(Notification).where(Notification.dedupe_key == dedupe_key)
        ).first()
        if existing is not None:
            return existing

    notification_kind = (
        notification_type.value
        if isinstance(notification_type, NotificationType)
        else str(notification_type)
    )
    notification = Notification(
        user_id=user_id,
        type=notification_kind,
        title=title,
        body=body,
        data=json.dumps(data) if data else None,
        dedupe_key=dedupe_key,
    )
    session.add(notification)

    try:
        # Assign the notification primary key before delivery rows reference it.
        session.flush()
        devices = session.exec(
            select(FCMToken).where(
                FCMToken.user_id == user_id,
                FCMToken.is_active == True,  # noqa: E712
                FCMToken.token.is_not(None),
            )
        ).all()
        for device in devices:
            session.add(
                NotificationDelivery(
                    notification_id=notification.id,
                    fcm_token_id=device.id,
                )
            )
        session.flush()

        if commit:
            session.commit()
            session.refresh(notification)
    except Exception:
        if commit:
            session.rollback()
        raise

    return notification


def send_notification_push(session: Session, notification: Notification) -> None:
    """Deprecated compatibility shim; delivery is handled by the P3.1 worker."""
    return None
