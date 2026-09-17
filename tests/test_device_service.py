"""P3.1 device-installation lifecycle and ownership-safety contracts."""

from sqlmodel import select

from app.db.models import (
    DeliveryStatus,
    FCMToken,
    Notification,
    NotificationDelivery,
    NotificationType,
)

API = "/api/v1"


def _register(client, device_id: str, token: str, device_info: str = "android"):
    return client.post(
        f"{API}/profile/fcm-token",
        json={
            "device_id": device_id,
            "fcm_token": token,
            "device_info": device_info,
        },
    )


def test_device_id_is_required(recipient_client):
    response = recipient_client.post(
        f"{API}/profile/fcm-token",
        json={"fcm_token": "missing-device-id"},
    )
    assert response.status_code == 422


def test_two_devices_can_coexist_for_one_user(recipient_client, session, sample_user):
    assert _register(recipient_client, "phone", "token-phone").status_code == 200
    assert _register(recipient_client, "tablet", "token-tablet").status_code == 200

    session.expire_all()
    rows = session.exec(
        select(FCMToken)
        .where(FCMToken.user_id == sample_user.id)
        .order_by(FCMToken.device_id)
    ).all()

    assert [(row.device_id, row.token) for row in rows] == [
        ("phone", "token-phone"),
        ("tablet", "token-tablet"),
    ]
    assert all(row.is_active for row in rows)


def test_same_device_token_rotation_updates_only_that_installation(
    recipient_client, session, sample_user
):
    assert _register(recipient_client, "phone", "phone-v1").status_code == 200
    assert _register(recipient_client, "tablet", "tablet-v1").status_code == 200
    assert _register(recipient_client, "phone", "phone-v2").status_code == 200

    session.expire_all()
    rows = session.exec(
        select(FCMToken)
        .where(FCMToken.user_id == sample_user.id)
        .order_by(FCMToken.device_id)
    ).all()

    assert [(row.device_id, row.token) for row in rows] == [
        ("phone", "phone-v2"),
        ("tablet", "tablet-v1"),
    ]


def test_token_transfer_clears_old_installation_and_skips_old_pending_jobs(
    recipient_client, donor_client, session, sample_user, donor_user
):
    shared_token = "shared-physical-token"
    assert _register(recipient_client, "recipient-phone", shared_token).status_code == 200

    old_installation = session.exec(
        select(FCMToken).where(FCMToken.token == shared_token)
    ).one()
    old_installation_id = old_installation.id

    notification = Notification(
        user_id=sample_user.id,
        type=NotificationType.NEW_BLOOD_REQUEST.value,
        title="Old owner alert",
        body="body",
    )
    session.add(notification)
    session.flush()
    delivery = NotificationDelivery(
        notification_id=notification.id,
        fcm_token_id=old_installation.id,
        status=DeliveryStatus.PENDING.value,
    )
    session.add(delivery)
    session.commit()
    delivery_id = delivery.id

    assert _register(donor_client, "donor-phone", shared_token).status_code == 200

    session.expire_all()
    old_installation = session.get(FCMToken, old_installation_id)
    old_delivery = session.get(NotificationDelivery, delivery_id)
    new_installation = session.exec(
        select(FCMToken).where(
            FCMToken.user_id == donor_user.id,
            FCMToken.device_id == "donor-phone",
        )
    ).one()

    assert old_installation.user_id == sample_user.id
    assert old_installation.token is None
    assert old_installation.is_active is False
    assert old_delivery.status == DeliveryStatus.SKIPPED.value
    assert new_installation.user_id == donor_user.id
    assert new_installation.token == shared_token
    assert new_installation.is_active is True


def test_unregister_is_404_for_non_owned_device(
    recipient_client, donor_client, session, sample_user
):
    assert _register(recipient_client, "private-phone", "private-token").status_code == 200

    response = donor_client.delete(f"{API}/profile/fcm-token/private-phone")
    assert response.status_code == 404

    session.expire_all()
    stored = session.exec(
        select(FCMToken).where(
            FCMToken.user_id == sample_user.id,
            FCMToken.device_id == "private-phone",
        )
    ).one()
    assert stored.is_active is True
    assert stored.token == "private-token"


def test_unregister_preserves_delivered_history(recipient_client, session, sample_user):
    assert _register(recipient_client, "phone", "history-token").status_code == 200
    installation = session.exec(
        select(FCMToken).where(
            FCMToken.user_id == sample_user.id,
            FCMToken.device_id == "phone",
        )
    ).one()

    notification = Notification(
        user_id=sample_user.id,
        type=NotificationType.PROFILE_REMINDER.value,
        title="Delivered already",
        body="body",
    )
    session.add(notification)
    session.flush()
    delivered = NotificationDelivery(
        notification_id=notification.id,
        fcm_token_id=installation.id,
        status=DeliveryStatus.DELIVERED.value,
    )
    session.add(delivered)
    session.commit()
    delivery_id = delivered.id

    response = recipient_client.delete(f"{API}/profile/fcm-token/phone")
    assert response.status_code == 200

    session.expire_all()
    installation = session.get(FCMToken, installation.id)
    delivered = session.get(NotificationDelivery, delivery_id)
    assert installation.is_active is False
    assert installation.token is None
    assert delivered.status == DeliveryStatus.DELIVERED.value
