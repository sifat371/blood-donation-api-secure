"""P3.1 durable request fan-out contracts."""

import json

from sqlmodel import select

from app.db.models import OutboxEvent

from .conftest import valid_request_payload

API = "/api/v1"


def test_request_creation_persists_one_outbox_event_atomically(
    recipient_client, session
):
    response = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["id"]

    events = list(session.exec(select(OutboxEvent)).all())
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "blood_request_created"
    assert event.aggregate_type == "blood_request"
    assert event.aggregate_id == str(request_id)
    assert event.idempotency_key == f"blood_request_created:{request_id}"
    assert json.loads(event.payload_json) == {"request_id": request_id}
