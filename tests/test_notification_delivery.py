"""P3.1 transactional inbox and durable per-device delivery contracts."""

from sqlmodel import select

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
