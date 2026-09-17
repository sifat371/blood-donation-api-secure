"""F0 regression contract for the backend surface consumed by the frontend."""

from datetime import timedelta

import pytest

from app.core.time import business_today
from app.db.models import BloodGroup, Notification, NotificationType, OutboxEvent
from app.mcp_tools.tools import create_blood_request
from app.services.notifications import create_notification
from tests.conftest import valid_request_payload

API = "/api/v1"


def test_donor_search_returns_compatible_nonidentical_blood_group(
    recipient_client, donor_user, third_user
):
    """O+ can donate red cells to A+; AB- cannot."""
    response = recipient_client.get(
        f"{API}/donors/search",
        params={
            "blood_group": BloodGroup.A_POS.value,
            "latitude": 23.7461,
            "longitude": 90.3742,
            "radius_km": 50,
        },
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert donor_user.id in ids
    assert third_user.id not in ids


def test_nearby_feed_hides_incompatible_request_from_donor(
    recipient_client, third_client
):
    request_id = recipient_client.post(
        f"{API}/blood-requests",
        json=valid_request_payload(blood_group=BloodGroup.O_POS.value),
    ).json()["id"]

    response = third_client.get(
        f"{API}/blood-requests/nearby",
        params={"latitude": 23.8223, "longitude": 90.3654, "radius_km": 50},
    )
    assert response.status_code == 200, response.text
    assert request_id not in {item["id"] for item in response.json()["items"]}


@pytest.mark.parametrize("state", ["incomplete", "unavailable", "recent"])
def test_nearby_feed_hides_requests_when_donor_cannot_accept(
    state, recipient_client, donor_client, donor_user, session
):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]

    if state == "incomplete":
        donor_user.phone = None
    elif state == "unavailable":
        donor_user.is_available = False
    else:
        donor_user.last_donation_date = business_today() - timedelta(days=10)
    session.add(donor_user)
    session.commit()

    response = donor_client.get(
        f"{API}/blood-requests/nearby",
        params={"latitude": 23.7925, "longitude": 90.4078, "radius_km": 50},
    )
    assert response.status_code == 200, response.text
    assert request_id not in {item["id"] for item in response.json()["items"]}


def test_active_commitments_endpoint_tracks_donor_commitment(
    recipient_client, donor_client
):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=2)
    ).json()["id"]
    accepted = donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert accepted.status_code == 200, accepted.text

    active = donor_client.get(f"{API}/blood-requests/commitments/mine")
    assert active.status_code == 200, active.text
    body = active.json()
    assert body["total"] == 1
    assert body["items"][0]["commitment"]["status"] == "Committed"
    assert body["items"][0]["request"]["id"] == request_id
    # A secured donor may see the private contact fields needed to coordinate.
    assert body["items"][0]["request"]["contact_number"] == "+8801711111111"

    withdrawn = donor_client.post(f"{API}/blood-requests/{request_id}/withdraw")
    assert withdrawn.status_code == 200, withdrawn.text
    assert donor_client.get(f"{API}/blood-requests/commitments/mine").json()["total"] == 0


def test_recipient_can_release_one_committed_donor_and_reopen_capacity(
    recipient_client, donor_client
):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=1)
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    commitment_id = recipient_client.get(
        f"{API}/blood-requests/{request_id}/commitments"
    ).json()[0]["id"]

    released = recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/release"
    )
    assert released.status_code == 200, released.text
    assert released.json()["units_committed"] == 0
    assert released.json()["remaining_units"] == 1
    assert released.json()["status"] == "Pending"

    donor_alerts = donor_client.get(f"{API}/notifications").json()["items"]
    assert any(
        item["type"] == "Commitment Released"
        and item["data"]["commitment_id"] == commitment_id
        for item in donor_alerts
    )


def test_completed_commitment_cannot_be_released(recipient_client, donor_client):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=1)
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    commitment_id = recipient_client.get(
        f"{API}/blood-requests/{request_id}/commitments"
    ).json()[0]["id"]
    recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/confirm"
    )

    response = recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/release"
    )
    assert response.status_code == 409


@pytest.mark.parametrize("missing", ["latitude", "longitude"])
def test_request_creation_requires_both_coordinates(recipient_client, missing):
    payload = valid_request_payload()
    payload.pop(missing)
    response = recipient_client.post(f"{API}/blood-requests", json=payload)
    assert response.status_code == 422, response.text


def test_mcp_request_creation_uses_durable_outbox(session, sample_user):
    result = create_blood_request(
        session,
        sample_user,
        patient_name="Chat Patient",
        blood_group=BloodGroup.O_POS.value,
        hospital_name="Chat Hospital",
        hospital_address="Dhaka",
        latitude=23.73,
        longitude=90.40,
        needed_date=str(business_today() + timedelta(days=1)),
        contact_number="+8801700000999",
        units=1,
    )
    assert "error" not in result
    request_id = result["id"]

    events = session.query(OutboxEvent).all()
    matching = [
        event
        for event in events
        if event.event_type == "blood_request_created"
        and event.aggregate_id == str(request_id)
    ]
    assert len(matching) == 1
    assert matching[0].idempotency_key == f"blood_request_created:{request_id}"


def test_notification_data_is_returned_as_object(recipient_client, session, sample_user):
    create_notification(
        session,
        sample_user.id,
        NotificationType.NEW_BLOOD_REQUEST,
        "Deep link",
        "Open request",
        data={"request_id": 42, "screen": "request"},
    )

    item = recipient_client.get(f"{API}/notifications").json()["items"][0]
    assert item["data"] == {"request_id": 42, "screen": "request"}


def test_invalid_historical_notification_data_becomes_null(
    recipient_client, session, sample_user
):
    row = Notification(
        user_id=sample_user.id,
        type=NotificationType.PROFILE_REMINDER.value,
        title="Legacy",
        body="Legacy malformed payload",
        data="{not-json",
    )
    session.add(row)
    session.commit()

    response = recipient_client.get(f"{API}/notifications")
    assert response.status_code == 200, response.text
    item = next(item for item in response.json()["items"] if item["id"] == row.id)
    assert item["data"] is None
