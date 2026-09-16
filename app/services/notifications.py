"""
Notification service — centralises FCM push + DB row creation.

Every trigger point (request created/accepted/cancelled/completed,
profile reminder, eligibility reminder) goes through this module.
"""

import json
import logging
from typing import Optional

from sqlmodel import Session

from firebase_admin import messaging
from sqlmodel import select

from app.core.firebase import is_fcm_available
from app.db.models import Notification, NotificationType, FCMToken

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
) -> Notification:
    """
    Create a DB notification row and optionally send an FCM push.

    Parameters
    ----------
    session : SQLModel Session
    user_id : target user
    notification_type : NotificationType enum member
    title : notification title
    body : notification body text
    data : optional JSON-serialisable dict (e.g. request_id, donor_id)
    send_push : attempt FCM delivery (no-op when credentials aren't configured)
    """
    notification = Notification(
        user_id=user_id,
        type=notification_type.value,
        title=title,
        body=body,
        data=json.dumps(data) if data else None,
    )
    session.add(notification)
    session.commit()
    session.refresh(notification)

    if send_push:
        _send_fcm_push(session, user_id, title, body, data)

    return notification


def _send_fcm_push(
    session: Session,
    user_id: int,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> None:
    """
    Send a push notification via Firebase Cloud Messaging.

    Best-effort by design: the in-app notification row has already been
    committed by the caller, so a delivery failure (no credentials, stale
    device token, no network) must never propagate and fail the API request
    that triggered it.
    """
    try:
        _send_fcm_push_unguarded(session, user_id, title, body, data)
    except Exception as exc:  # noqa: BLE001 — see the guarantee above
        # The per-token handler below covers send failures. This outer guard
        # covers everything around them — the availability check, the token
        # query, payload coercion — because the caller has already committed the
        # notification row. Letting an exception through here would turn a
        # successful accept/complete into an HTTP 500 for an operation that in
        # fact went through, and the retry would then fail as "already accepted".
        logger.warning(
            "FCM push to user_id=%s failed before delivery: %s", user_id, type(exc).__name__
        )


def _send_fcm_push_unguarded(
    session: Session,
    user_id: int,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> None:
    """Body of `_send_fcm_push`; call that instead — it holds the guarantee."""
    if not is_fcm_available():
        logger.debug(
            "Skipping FCM push to user_id=%s — Firebase is not configured", user_id
        )
        return

    logger.info("FCM push → user_id=%s  title=%r", user_id, title)

    stmt = select(FCMToken).where(FCMToken.user_id == user_id)
    tokens = session.exec(stmt).all()

    # FCM data payloads must be string→string.
    str_data = {k: str(v) for k, v in data.items()} if data else {}

    for user_token in tokens:
        try:
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data=str_data,
                token=user_token.token,
            )
            response = messaging.send(message)
            logger.info("FCM message sent: %s", response)
        except Exception as exc:  # noqa: BLE001 — delivery is best-effort
            # Log the token id, not the token value.
            logger.warning(
                "FCM delivery failed for token id=%s (user_id=%s): %s",
                user_token.id,
                user_id,
                exc,
            )
