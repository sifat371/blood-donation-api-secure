"""Review-found P3.1 invariants that protect worker/device concurrency."""

import json
from datetime import timedelta

from sqlmodel import select

from app.core.config import settings
from app.core.time import business_today, utc_now
from app.db.models import (
    BloodGroup,
    BloodRequest,
    DeliveryStatus,
    FCMToken,
    Notification,
    NotificationDelivery,
    NotificationType,
    OutboxEvent,
    RequestStatus,
)
from app.services.device_service import skip_unresolved_deliveries
from app.workers.outbox_processor import process_outbox_event


def test_skip_unresolved_deliveries_preserves_fresh_processing_claim(
    session, sample_user
):
    device = FCMToken(
        user_id=sample_user.id,
        device_id="claimed-phone",
        token="claimed-token",
        device_info="android",
    )
    session.add(device)
    session.flush()

    notification = Notification(
        user_id=sample_user.id,
        type=NotificationType.PROFILE_REMINDER.value,
        title="Concurrency check",
        body="body",
    )
    session.add(notification)
    session.flush()

    now = utc_now()
    fresh = NotificationDelivery(
        notification_id=notification.id,
        fcm_token_id=device.id,
        status=DeliveryStatus.PROCESSING.value,
        locked_at=now,
        locked_by="worker-fresh",
    )
    stale = NotificationDelivery(
        notification_id=notification.id,
        fcm_token_id=device.id,
        status=DeliveryStatus.PROCESSING.value,
        locked_at=now
        - timedelta(seconds=max(settings.notification_worker_lease_seconds, 1) + 1),
        locked_by="worker-stale",
    )
    pending = NotificationDelivery(
        notification_id=notification.id,
        fcm_token_id=device.id,
        status=DeliveryStatus.PENDING.value,
    )
    # The model's uniqueness constraint allows only one delivery per
    # notification/device pair, so use separate notifications for stale/pending.
    stale_notification = Notification(
        user_id=sample_user.id,
        type=NotificationType.PROFILE_REMINDER.value,
        title="Stale claim",
        body="body",
    )
    pending_notification = Notification(
        user_id=sample_user.id,
        type=NotificationType.PROFILE_REMINDER.value,
        title="Pending",
        body="body",
    )
    session.add(stale_notification)
    session.add(pending_notification)
    session.flush()
    stale.notification_id = stale_notification.id
    pending.notification_id = pending_notification.id
    session.add(fresh)
    session.add(stale)
    session.add(pending)
    session.commit()

    changed = skip_unresolved_deliveries(session, device.id, "device_unregistered")
    session.commit()
    session.expire_all()

    assert changed == 2
    assert session.get(NotificationDelivery, fresh.id).status == DeliveryStatus.PROCESSING.value
    assert session.get(NotificationDelivery, stale.id).status == DeliveryStatus.SKIPPED.value
    assert session.get(NotificationDelivery, pending.id).status == DeliveryStatus.SKIPPED.value


def test_delayed_fanout_expires_overdue_unsecured_request_instead_of_notifying(
    session, sample_user, donor_user
):
    request = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Expired patient",
        blood_group=BloodGroup.O_POS.value,
        units=1,
        hospital_name="Expired Hospital",
        latitude=sample_user.latitude,
        longitude=sample_user.longitude,
        needed_date=business_today() - timedelta(days=1),
        contact_number=sample_user.phone,
        status=RequestStatus.PENDING.value,
    )
    session.add(request)
    session.flush()
    event = OutboxEvent(
        event_type="blood_request_created",
        aggregate_type="blood_request",
        aggregate_id=str(request.id),
        payload_json=json.dumps({"request_id": request.id}),
        idempotency_key=f"blood_request_created:{request.id}",
    )
    session.add(event)
    session.commit()

    process_outbox_event(session, event.id)
    session.expire_all()

    stored = session.get(BloodRequest, request.id)
    assert stored.status == RequestStatus.EXPIRED.value
    notifications = session.exec(
        select(Notification).where(
            Notification.dedupe_key.like(
                f"blood_request_created:{request.id}:donor:%"
            )
        )
    ).all()
    assert notifications == []
