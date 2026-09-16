"""P3.1 durable request fan-out contracts."""

import json

from sqlmodel import select

from app.db.models import FCMToken, Notification, NotificationDelivery, OutboxEvent

from .conftest import valid_request_payload

API = "/api/v1"


def _event(session) -> OutboxEvent:
    events = list(session.exec(select(OutboxEvent)).all())
    assert len(events) == 1
    return events[0]


def test_request_creation_persists_one_outbox_event_atomically(
    recipient_client, session
):
    response = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["id"]

    event = _event(session)
    assert event.event_type == "blood_request_created"
    assert event.aggregate_type == "blood_request"
    assert event.aggregate_id == str(request_id)
    assert event.idempotency_key == f"blood_request_created:{request_id}"
    assert json.loads(event.payload_json) == {"request_id": request_id}


def test_replayed_fanout_event_does_not_duplicate_inbox_or_delivery_rows(
    recipient_client, session, donor_user
):
    device = FCMToken(
        user_id=donor_user.id,
        device_id="donor-phone",
        token="durable-fanout-token",
        device_info="android",
    )
    session.add(device)
    session.commit()
    session.refresh(device)

    response = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    )
    request_id = response.json()["id"]
    event = _event(session)

    from app.workers.outbox_processor import process_outbox_event

    process_outbox_event(session, event.id)
    process_outbox_event(session, event.id)

    key = f"blood_request_created:{request_id}:donor:{donor_user.id}"
    notifications = list(
        session.exec(select(Notification).where(Notification.dedupe_key == key)).all()
    )
    assert len(notifications) == 1
    deliveries = list(
        session.exec(
            select(NotificationDelivery).where(
                NotificationDelivery.notification_id == notifications[0].id
            )
        ).all()
    )
    assert len(deliveries) == 1
    assert deliveries[0].fcm_token_id == device.id


def test_fanout_noops_if_request_is_cancelled_before_processing(
    recipient_client, session
):
    response = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    )
    request_id = response.json()["id"]
    event = _event(session)
    assert recipient_client.post(f"{API}/blood-requests/{request_id}/cancel").status_code == 200

    from app.workers.outbox_processor import process_outbox_event

    process_outbox_event(session, event.id)

    fanout = list(
        session.exec(
            select(Notification).where(
                Notification.dedupe_key.like(
                    f"blood_request_created:{request_id}:donor:%"
                )
            )
        ).all()
    )
    assert fanout == []
