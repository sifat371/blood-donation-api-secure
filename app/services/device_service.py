"""FCM installation registration, transfer, and unregister semantics."""

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utc_now
from app.db.models import DeliveryStatus, FCMToken, NotificationDelivery


def skip_unresolved_deliveries(
    session: Session,
    fcm_token_id: int,
    reason: str,
) -> int:
    """Skip deliverable jobs after an installation becomes unusable.

    Pending/retry jobs are safe to cancel immediately. A Processing job belongs
    to a live worker claim and is skipped only when that claim has no lock time
    or its lease has expired; fresh in-flight work must be allowed to finish its
    own state transition.
    """
    now = utc_now()
    cutoff = now - timedelta(
        seconds=max(settings.notification_worker_lease_seconds, 1)
    )
    rows = session.exec(
        select(NotificationDelivery).where(
            NotificationDelivery.fcm_token_id == fcm_token_id,
            or_(
                NotificationDelivery.status.in_(
                    (DeliveryStatus.PENDING.value, DeliveryStatus.RETRY.value)
                ),
                and_(
                    NotificationDelivery.status == DeliveryStatus.PROCESSING.value,
                    or_(
                        NotificationDelivery.locked_at.is_(None),
                        NotificationDelivery.locked_at < cutoff,
                    ),
                ),
            ),
        )
    ).all()
    for delivery in rows:
        delivery.status = DeliveryStatus.SKIPPED.value
        delivery.last_error = reason
        delivery.last_error_category = reason
        delivery.locked_at = None
        delivery.locked_by = None
        delivery.updated_at = now
        session.add(delivery)
    if rows:
        session.flush()
    return len(rows)


def register_device(
    session: Session,
    user_id: int,
    device_id: str,
    fcm_token: str,
    device_info: str,
) -> FCMToken:
    """Upsert one installation while keeping an FCM token owned by one account."""
    now = utc_now()
    try:
        conflict = session.exec(
            select(FCMToken).where(FCMToken.token == fcm_token)
        ).first()
        if conflict is not None and (
            conflict.user_id != user_id or conflict.device_id != device_id
        ):
            skip_unresolved_deliveries(
                session,
                conflict.id,
                "token_transferred",
            )
            conflict.token = None
            conflict.is_active = False
            conflict.disabled_at = now
            conflict.last_failure_reason = "token_transferred"
            conflict.updated_at = now
            session.add(conflict)
            # Release the globally unique token before assigning it elsewhere.
            session.flush()

        installation = session.exec(
            select(FCMToken).where(
                FCMToken.user_id == user_id,
                FCMToken.device_id == device_id,
            )
        ).first()
        if installation is None:
            installation = FCMToken(
                user_id=user_id,
                device_id=device_id,
                token=fcm_token,
                device_info=device_info or "android",
                is_active=True,
                last_seen_at=now,
                updated_at=now,
                created_at=now,
            )
        else:
            installation.token = fcm_token
            installation.device_info = device_info or installation.device_info or "android"
            installation.is_active = True
            installation.last_seen_at = now
            installation.updated_at = now
            installation.disabled_at = None
            installation.last_failure_reason = None

        session.add(installation)
        session.commit()
        session.refresh(installation)
        return installation
    except Exception:
        session.rollback()
        raise


def unregister_device(
    session: Session,
    user_id: int,
    device_id: str,
) -> FCMToken:
    """Disable one caller-owned installation without erasing delivery history."""
    installation = session.exec(
        select(FCMToken).where(
            FCMToken.user_id == user_id,
            FCMToken.device_id == device_id,
        )
    ).first()
    if installation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FCM device not found",
        )

    now = utc_now()
    try:
        skip_unresolved_deliveries(session, installation.id, "device_unregistered")
        installation.token = None
        installation.is_active = False
        installation.disabled_at = now
        installation.last_failure_reason = "device_unregistered"
        installation.updated_at = now
        session.add(installation)
        session.commit()
        session.refresh(installation)
        return installation
    except Exception:
        session.rollback()
        raise
