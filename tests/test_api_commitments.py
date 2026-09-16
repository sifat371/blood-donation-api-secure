"""P2 REST contract for multi-donor blood-request commitments."""

from fastapi.testclient import TestClient

from app.db.models import BloodGroup, User
from app.main import app
from app.services.auth_service import create_access_token
from tests.conftest import valid_request_payload


def _second_donor_client(session) -> tuple[User, TestClient]:
    donor = User(
        name="Second Donor",
        email="second-donor@example.com",
        email_verified=True,
        phone="+8801700000099",
        blood_group=BloodGroup.O_POS.value,
        is_available=True,
        gender="Male",
        latitude=23.78,
        longitude=90.40,
    )
    session.add(donor)
    session.commit()
    session.refresh(donor)
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {create_access_token(donor.id)}"})
    return donor, client


def test_accept_exposes_multi_donor_unit_counts(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=2)
    ).json()

    response = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Partially Committed"
    assert body["units"] == 2
    assert body["units_required"] == 2
    assert body["units_committed"] == 1
    assert body["units_completed"] == 0
    assert body["remaining_units"] == 1
    assert body["my_commitment_status"] == "Committed"


def test_recipient_lists_all_commitments_but_donor_lists_only_own(
    recipient_client, donor_client, session
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=2)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    second_donor, second_client = _second_donor_client(session)
    second_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    owner_view = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}"
    ).json()
    assert owner_view["status"] == "Fully Committed"
    assert owner_view["units_committed"] == 2
    assert owner_view["remaining_units"] == 0
    assert owner_view["accepted_by"] is None
    assert owner_view["donor_name"] is None
    assert owner_view["donor_phone"] is None

    owner_commitments = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    )
    assert owner_commitments.status_code == 200, owner_commitments.text
    items = owner_commitments.json()
    assert len(items) == 2
    assert {item["donor_name"] for item in items} == {"Donor User", "Second Donor"}
    assert {item["donor_phone"] for item in items} == {
        "+8801700000001",
        second_donor.phone,
    }

    donor_commitments = donor_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    )
    assert donor_commitments.status_code == 200, donor_commitments.text
    donor_items = donor_commitments.json()
    assert len(donor_items) == 1
    assert donor_items[0]["donor_name"] == "Donor User"
    assert donor_items[0]["donor_phone"] == "+8801700000001"


def test_unrelated_user_cannot_list_commitments(
    recipient_client, donor_client, third_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=2)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    response = third_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    )
    assert response.status_code == 404


def test_withdraw_reopens_capacity(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=1)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    response = donor_client.post(f"/api/v1/blood-requests/{created['id']}/withdraw")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "Pending"
    assert body["units_committed"] == 0
    assert body["units_completed"] == 0
    assert body["remaining_units"] == 1
    assert body["my_commitment_status"] == "Withdrawn"


def test_confirm_one_unit_and_complete_route_requires_all_units(
    recipient_client, donor_client, session
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=2)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    _, second_client = _second_donor_client(session)
    second_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    commitments = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    ).json()
    first_id = commitments[0]["id"]
    second_id = commitments[1]["id"]

    first = recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/commitments/{first_id}/confirm"
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "Fully Committed"
    assert body["units_committed"] == 1
    assert body["units_completed"] == 1
    assert body["remaining_units"] == 0

    premature = recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/complete"
    )
    assert premature.status_code == 409

    second = recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/commitments/{second_id}/confirm"
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "Completed"
    assert second.json()["units_completed"] == 2

    compatibility = recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/complete"
    )
    assert compatibility.status_code == 200, compatibility.text
    assert compatibility.json()["status"] == "Completed"
