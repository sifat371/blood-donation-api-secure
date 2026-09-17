"""P3.1 transactional inbox and durable per-device delivery contracts."""

import json
from datetime import timedelta

from sqlmodel import select

from app.core.time import utc_now
from app.db.models import (
    DeliveryStatus,
    FCMToken,
    Notification,
    NotificationDelivery,
    NotificationType,
)
from app.services.notifications import create_notification

from .conftest import valid_request_payload

API = "/api/v1"


def _device(session, user_id: int, device_id: str, token: str, *, active: bool = True):
    row = FCMToken(
        user_id=user_id,
        device_id=device_id,
        token=token,
        device_info="android",
        is_active=active,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _deliveries(session, notification_id: int):
    return session.exec(
        select(NotificationDelivery).where(
            NotificationDelivery.notification_id == notification_id
        )
    ).all()


def _notification_with_delivery(session, sample_user, *, data=None):
    device = _device(
        session,
        sample_user.id,
        f"phone-{sample_user.id}",
        f"token-{sample_user.id}",
    )
    notification = create_notification(
        session,
        sample_user.id,
        NotificationType.NEW_BLOOD_REQUEST,
        "Blood needed",
        "Open the request for details.",
        data=data,
    )
    delivery = _deliveries(session, notification.id)[0]
    return notification, device, delivery


def test_notification_creates_one_delivery_per_active_device(session, sample_user):
    phone = _device(session, sample_user.id, "phone", "phone-token")
    tablet = _device(session, sample_user.id, "tablet", "tablet-token")
    _device(session, sample_user.id, "old", "old-token", active=False)

    notification = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Profile reminder",
        "Please update your profile.",
    )

    rows = _deliveries(session, notification.id)
    assert {row.fcm_token_id for row in rows} == {phone.id, tablet.id}
    assert all(row.status == DeliveryStatus.PENDING.value for row in rows)


def test_inactive_devices_receive_no_new_delivery(session, sample_user):
    inactive = _device(session, sample_user.id, "old", "old-token", active=False)

    notification = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Profile reminder",
        "body",
    )

    assert all(
        row.fcm_token_id != inactive.id for row in _deliveries(session, notification.id)
    )


def test_zero_device_user_still_gets_inbox_notification(session, sample_user):
    notification = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Inbox only",
        "body",
    )

    assert notification.id is not None
    assert _deliveries(session, notification.id) == []


def test_rollback_removes_notification_and_deliveries(session, sample_user):
    _device(session, sample_user.id, "phone", "rollback-token")

    notification = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Transactional",
        "body",
        commit=False,
    )
    notification_id = notification.id
    assert len(_deliveries(session, notification_id)) == 1

    session.rollback()

    assert session.get(Notification, notification_id) is None
    assert _deliveries(session, notification_id) == []


def test_same_dedupe_key_returns_same_notification_without_duplicate_jobs(
    session, sample_user
):
    _device(session, sample_user.id, "phone", "dedupe-token")
    key = "test:event:recipient"

    first = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Once",
        "body",
        dedupe_key=key,
    )
    second = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Once again",
        "different body",
        dedupe_key=key,
    )

    assert second.id == first.id
    assert len(_deliveries(session, first.id)) == 1
    rows = session.exec(select(Notification).where(Notification.dedupe_key == key)).all()
    assert len(rows) == 1


def test_accept_makes_no_synchronous_fcm_call(
    recipient_client, donor_client, monkeypatch
):
    import app.services.notifications as notifications_module

    calls = []
    monkeypatch.setattr(notifications_module, "is_fcm_available", lambda: True)
    monkeypatch.setattr(
        notifications_module.messaging,
        "send",
        lambda message: calls.append(message) or "provider-id",
    )
    assert recipient_client.post(
        f"{API}/profile/fcm-token",
        json={"device_id": "recipient-phone", "fcm_token": "recipient-push"},
    ).status_code == 200

    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    assert donor_client.post(f"{API}/blood-requests/{request_id}/accept").status_code == 200

    assert calls == []


def test_confirm_makes_no_synchronous_fcm_call(
    recipient_client, donor_client, monkeypatch
):
    import app.services.notifications as notifications_module

    calls = []
    monkeypatch.setattr(notifications_module, "is_fcm_available", lambda: True)
    monkeypatch.setattr(
        notifications_module.messaging,
        "send",
        lambda message: calls.append(message) or "provider-id",
    )
    assert donor_client.post(
        f"{API}/profile/fcm-token",
        json={"device_id": "donor-phone", "fcm_token": "donor-push"},
    ).status_code == 200

    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=1)
    ).json()["id"]
    assert donor_client.post(f"{API}/blood-requests/{request_id}/accept").status_code == 200
    calls.clear()

    commitment_id = recipient_client.get(
        f"{API}/blood-requests/{request_id}/commitments"
    ).json()[0]["id"]
    assert recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/confirm"
    ).status_code == 200

    assert calls == []


# ── Worker-owned FCM delivery state machine ─────────────


def test_payload_contains_server_ids_type_and_deep_link_fields(session, sample_user):
    from app.workers.delivery_processor import build_push_payload

    notification, _, _ = _notification_with_delivery(
        session,
        sample_user,
        data={
            "request_id": 42,
            "notification_id": "spoofed",
            "event_id": "spoofed",
            "type": "spoofed",
        },
    )

    payload = build_push_payload(notification)

    assert payload["request_id"] == "42"
    assert payload["notification_id"] == str(notification.id)
    assert payload["event_id"] == notification.event_id
    assert payload["type"] == notification.type


def test_success_marks_delivery_delivered_and_records_provider_id(session, sample_user):
    from app.workers.delivery_processor import process_delivery

    _, _, delivery = _notification_with_delivery(session, sample_user, data={"request_id": 7})

    process_delivery(session, delivery.id, send=lambda message: "projects/test/messages/123")

    session.expire_all()
    stored = session.get(NotificationDelivery, delivery.id)
    assert stored.status == DeliveryStatus.DELIVERED.value
    assert stored.provider_message_id == "projects/test/messages/123"
    assert stored.delivered_at is not None
    assert stored.attempts == 1
    assert stored.locked_at is None
    assert stored.locked_by is None


def test_transient_failure_schedules_retry_in_future(session, sample_user):
    from app.workers.delivery_processor import process_delivery

    _, _, delivery = _notification_with_delivery(session, sample_user)
    before = utc_now()

    def fail(_message):
        raise TimeoutError("provider timeout")

    process_delivery(session, delivery.id, send=fail, jitter=0.0)

    session.expire_all()
    stored = session.get(NotificationDelivery, delivery.id)
    assert stored.status == DeliveryStatus.RETRY.value
    assert stored.attempts == 1
    assert stored.available_at >= before + timedelta(seconds=60)
    assert stored.last_error_category == "transient"


def test_fifth_transient_failure_moves_delivery_dead(session, sample_user):
    from app.workers.delivery_processor import process_delivery

    _, _, delivery = _notification_with_delivery(session, sample_user)
    delivery.attempts = 4
    session.add(delivery)
    session.commit()

    def fail(_message):
        raise ConnectionError("FCM unavailable")

    process_delivery(session, delivery.id, send=fail, jitter=0.0)

    session.expire_all()
    stored = session.get(NotificationDelivery, delivery.id)
    assert stored.status == DeliveryStatus.DEAD.value
    assert stored.attempts == 5
    assert stored.last_error_category == "transient"


def test_unregistered_token_disables_device_and_skips_related_unresolved_jobs(
    session, sample_user
):
    from app.workers.delivery_processor import process_delivery

    _, device, first = _notification_with_delivery(session, sample_user)
    second_notification = create_notification(
        session,
        sample_user.id,
        NotificationType.PROFILE_REMINDER,
        "Second",
        "body",
    )
    second = _deliveries(session, second_notification.id)[0]

    class UnregisteredError(Exception):
        pass

    def fail(_message):
        raise UnregisteredError("Requested entity was not found")

    process_delivery(session, first.id, send=fail)

    session.expire_all()
    stored_device = session.get(FCMToken, device.id)
    stored_first = session.get(NotificationDelivery, first.id)
    stored_second = session.get(NotificationDelivery, second.id)
    assert stored_device.is_active is False
    assert stored_device.token is None
    assert stored_device.disabled_at is not None
    assert stored_device.last_failure_reason == "permanent_token"
    assert stored_first.status == DeliveryStatus.SKIPPED.value
    assert stored_second.status == DeliveryStatus.SKIPPED.value


def test_malformed_notification_payload_dead_letters_without_provider_call(
    session, sample_user
):
    from app.workers.delivery_processor import process_delivery

    notification, _, delivery = _notification_with_delivery(session, sample_user)
    notification.data = "{not-json"
    session.add(notification)
    session.commit()
    calls = []

    process_delivery(
        session,
        delivery.id,
        send=lambda message: calls.append(message) or "provider-id",
    )

    session.expire_all()
    stored = session.get(NotificationDelivery, delivery.id)
    assert calls == []
    assert stored.status == DeliveryStatus.DEAD.value
    assert stored.attempts == 1
    assert stored.last_error_category == "malformed"


def test_missing_firebase_configuration_keeps_work_retryable(
    session, sample_user, monkeypatch
):
    import app.workers.delivery_processor as delivery_processor

    _, _, delivery = _notification_with_delivery(session, sample_user)
    monkeypatch.setattr(delivery_processor, "is_fcm_available", lambda: False)

    process = delivery_processor.process_delivery
    process(session, delivery.id, jitter=0.0)

    session.expire_all()
    stored = session.get(NotificationDelivery, delivery.id)
    assert stored.status == DeliveryStatus.RETRY.value
    assert stored.attempts == 1
    assert stored.last_error_category == "transient"


def test_delivery_refuses_if_installation_owner_no_longer_matches_notification_owner(
    session, sample_user, donor_user
):
    from app.workers.delivery_processor import process_delivery

    _, device, delivery = _notification_with_delivery(session, sample_user)
    device.user_id = donor_user.id
    session.add(device)
    session.commit()
    calls = []

    process_delivery(
        session,
        delivery.id,
        send=lambda message: calls.append(message) or "provider-id",
    )

    session.expire_all()
    stored = session.get(NotificationDelivery, delivery.id)
    assert calls == []
    assert stored.status == DeliveryStatus.SKIPPED.value
    assert stored.last_error_category == "device_not_deliverable"


def test_retry_delay_seconds_uses_configured_schedule(monkeypatch):
    import app.workers.delivery_processor as delivery_processor

    monkeypatch.setattr(
        delivery_processor.settings,
        "notification_worker_retry_seconds",
        [10, 20, 30, 40, 50],
    )
    assert delivery_processor.retry_delay_seconds(1, jitter=0.0) == 10
    assert delivery_processor.retry_delay_seconds(4, jitter=0.0) == 40
