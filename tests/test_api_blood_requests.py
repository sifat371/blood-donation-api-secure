"""
Endpoint tests for the blood-request lifecycle.

Covers the P0 fixes: the units validation rule, GET /blood-requests/{id},
and the authorisation rules that keep two users' data separate.
"""

from datetime import date, datetime, timedelta

from app.db.models import BloodGroup, RequestStatus
from app.core.time import business_today
from tests.conftest import valid_request_payload


# ── Units validation (one rule, no HTTP 500s) ────────────


def test_create_accepts_single_unit(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=1)
    )
    assert r.status_code == 201, r.text
    assert r.json()["units"] == 1


def test_create_accepts_normal_units(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=3)
    )
    assert r.status_code == 201, r.text
    assert r.json()["units"] == 3


def test_create_accepts_maximum_units(recipient_client):
    """The old bug: create allowed 20 but the response model capped at 5,
    so anything from 6 upward returned HTTP 500 instead of a created row."""
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=10)
    )
    assert r.status_code == 201, r.text
    assert r.json()["units"] == 10


def test_create_rejects_units_above_maximum(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=11)
    )
    assert r.status_code == 422, r.text


def test_create_rejects_zero_units(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=0)
    )
    assert r.status_code == 422, r.text


def test_create_rejects_negative_units(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=-3)
    )
    assert r.status_code == 422, r.text


def test_no_units_value_produces_a_server_error(recipient_client):
    """Sweep the whole neighbourhood of the old 5-vs-20 mismatch."""
    for units in range(-1, 25):
        r = recipient_client.post(
            "/api/v1/blood-requests", json=valid_request_payload(units=units)
        )
        assert r.status_code in (201, 422), f"units={units} → {r.status_code} {r.text}"
        assert r.status_code < 500, f"units={units} caused a server error"


def test_create_rejects_out_of_range_coordinates(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(latitude=120.0)
    )
    assert r.status_code == 422, r.text


# ── Auth ─────────────────────────────────────────────────


def test_create_requires_authentication(client):
    r = client.post("/api/v1/blood-requests", json=valid_request_payload())
    assert r.status_code == 401


def test_get_by_id_requires_authentication(client, recipient_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    r = client.get(f"/api/v1/blood-requests/{created['id']}")
    assert r.status_code == 401


def test_invalid_token_is_rejected(client):
    r = client.get(
        "/api/v1/blood-requests/mine",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


# ── GET /blood-requests/{id} ─────────────────────────────


def test_owner_can_get_own_request_by_id(recipient_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = recipient_client.get(f"/api/v1/blood-requests/{created['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == created["id"]
    assert body["patient_name"] == "Karim Rahman"
    assert body["status"] == RequestStatus.PENDING.value
    assert body["recipient_name"] == "Test User"


def test_get_missing_request_returns_404(recipient_client):
    r = recipient_client.get("/api/v1/blood-requests/999999")
    assert r.status_code == 404


def test_donor_can_get_pending_request_by_id(recipient_client, donor_client):
    """The donor discovery path: donor opens someone else's pending request."""
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = donor_client.get(f"/api/v1/blood-requests/{created['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == created["id"]


def test_public_pending_request_redacts_patient_contact_and_exact_location(
    recipient_client, third_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    body = third_client.get(f"/api/v1/blood-requests/{created['id']}").json()
    assert body["patient_name"] is None
    assert body["contact_number"] is None
    assert body["hospital_address"] is None
    assert body["latitude"] is None
    assert body["longitude"] is None
    assert body["notes"] is None
    assert body["recipient_id"] is None
    assert body["recipient_name"] is None
    assert body["accepted_by"] is None
    # Matching-relevant information remains visible.
    assert body["blood_group"] == "O+"
    assert body["units"] == 2
    assert body["hospital_name"] == "Dhaka Medical College Hospital"


def test_third_party_cannot_see_cancelled_request(recipient_client, third_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    recipient_client.post(f"/api/v1/blood-requests/{created['id']}/cancel")

    r = third_client.get(f"/api/v1/blood-requests/{created['id']}")
    assert r.status_code == 404

    # ...but the owner still can.
    assert (
        recipient_client.get(f"/api/v1/blood-requests/{created['id']}").status_code
        == 200
    )


def test_donor_contact_is_hidden_from_third_parties(
    recipient_client, donor_client, third_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    # Recipient sees who is donating and how to reach them.
    owner_view = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}"
    ).json()
    assert owner_view["donor_name"] == "Donor User"
    assert owner_view["donor_phone"] == "+8801700000001"

    # The donor sees their own details too.
    donor_view = donor_client.get(f"/api/v1/blood-requests/{created['id']}").json()
    assert donor_view["donor_phone"] == "+8801700000001"

    # An unrelated account gets the request but not the donor's phone number.
    third_view = third_client.get(f"/api/v1/blood-requests/{created['id']}")
    assert third_view.status_code == 200
    third_body = third_view.json()
    assert third_body["donor_name"] is None
    assert third_body["donor_phone"] is None
    assert third_body["patient_name"] is None
    assert third_body["contact_number"] is None
    assert third_body["accepted_by"] is None


# ── Accept ───────────────────────────────────────────────


def test_donor_can_accept_pending_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == RequestStatus.ACCEPTED.value
    assert body["accepted_by"] is not None


def test_ineligible_recent_donor_cannot_accept_request(
    client, recipient_client, ineligible_donor
):
    from app.services.auth_service import create_access_token

    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    client.headers.update(
        {"Authorization": f"Bearer {create_access_token(ineligible_donor.id)}"}
    )

    r = client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400
    assert "eligible" in str(r.json()["detail"]).lower()


def test_unavailable_donor_cannot_accept_request(
    recipient_client, donor_client, donor_user, session
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_user.is_available = False
    session.add(donor_user)
    session.commit()

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400
    assert "available" in str(r.json()["detail"]).lower()


def test_incompatible_blood_group_cannot_accept_request(
    recipient_client, third_client
):
    # third_user is AB-, which is not a compatible red-cell donor for O+.
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(blood_group="O+")
    ).json()

    r = third_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400
    assert "compatible" in str(r.json()["detail"]).lower()


def test_compatible_nonidentical_blood_group_can_accept(
    recipient_client, donor_client
):
    # O+ red cells are compatible with an A+ recipient.
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(blood_group="A+")
    ).json()

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 200, r.text


def test_donor_with_incomplete_profile_cannot_accept(
    recipient_client, donor_client, donor_user, session
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_user.phone = None
    session.add(donor_user)
    session.commit()

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400
    assert "profile" in str(r.json()["detail"]).lower()


def test_cannot_accept_own_request(recipient_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400
    assert "own request" in r.json()["detail"]


def test_cannot_accept_an_already_accepted_request(
    recipient_client, donor_client, third_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    assert (
        donor_client.post(
            f"/api/v1/blood-requests/{created['id']}/accept"
        ).status_code
        == 200
    )

    r = third_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400


def test_cannot_accept_a_cancelled_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    recipient_client.post(f"/api/v1/blood-requests/{created['id']}/cancel")

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 400


def test_accept_missing_request_returns_404(donor_client):
    r = donor_client.post("/api/v1/blood-requests/999999/accept")
    assert r.status_code == 404


# ── Complete ─────────────────────────────────────────────


def test_recipient_can_complete_accepted_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == RequestStatus.COMPLETED.value


def test_donor_cannot_complete_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/complete")
    assert r.status_code == 403


def test_cannot_complete_a_pending_request(recipient_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")
    assert r.status_code == 400


def test_completion_records_donation_history_for_the_donor(
    recipient_client, donor_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")

    history = donor_client.get("/api/v1/profile/donation-history").json()
    assert history["total"] == 1
    assert history["items"][0]["hospital"] == "Dhaka Medical College Hospital"

    # ...and the recipient's own history stays empty.
    assert recipient_client.get("/api/v1/profile/donation-history").json()["total"] == 0


def test_completion_sets_donor_last_donation_date(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")

    donor = donor_client.get("/api/v1/profile/me").json()
    assert donor["last_donation_date"] == str(business_today())


# ── Cancel ───────────────────────────────────────────────


def test_recipient_can_cancel_pending_request(recipient_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == RequestStatus.CANCELLED.value


def test_other_users_cannot_cancel_someone_elses_request(
    recipient_client, donor_client, third_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    assert (
        third_client.post(
            f"/api/v1/blood-requests/{created['id']}/cancel"
        ).status_code
        == 403
    )

    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    # Even the accepted donor can't cancel the recipient's request.
    assert (
        donor_client.post(
            f"/api/v1/blood-requests/{created['id']}/cancel"
        ).status_code
        == 403
    )

    # Still Accepted, untouched.
    assert (
        recipient_client.get(f"/api/v1/blood-requests/{created['id']}").json()["status"]
        == RequestStatus.ACCEPTED.value
    )


def test_cannot_cancel_a_completed_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/cancel")
    assert r.status_code == 400


# ── Listing / isolation ──────────────────────────────────


def test_mine_only_returns_the_callers_requests(recipient_client, donor_client):
    recipient_client.post("/api/v1/blood-requests", json=valid_request_payload())
    donor_client.post(
        "/api/v1/blood-requests",
        json=valid_request_payload(patient_name="Someone Else"),
    )

    mine = recipient_client.get("/api/v1/blood-requests/mine").json()
    assert mine["total"] == 1
    assert mine["items"][0]["patient_name"] == "Karim Rahman"

    theirs = donor_client.get("/api/v1/blood-requests/mine").json()
    assert theirs["total"] == 1
    assert theirs["items"][0]["patient_name"] == "Someone Else"


def test_nearby_surfaces_another_users_pending_request(
    recipient_client, donor_client
):
    recipient_client.post("/api/v1/blood-requests", json=valid_request_payload())

    r = donor_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 23.7925, "longitude": 90.4078, "radius_km": 20},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["patient_name"] is None
    assert item["recipient_name"] is None
    assert item["contact_number"] is None
    assert item["latitude"] is None
    assert item["longitude"] is None
    assert item["hospital_name"] == "Dhaka Medical College Hospital"
    assert item["blood_group"] == "O+"
    assert item["distance_km"] is not None


def test_nearby_excludes_your_own_requests(recipient_client):
    recipient_client.post("/api/v1/blood-requests", json=valid_request_payload())

    r = recipient_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 23.7461, "longitude": 90.3742},
    )
    assert r.json()["total"] == 0


def test_nearby_respects_the_radius(recipient_client, donor_client):
    recipient_client.post("/api/v1/blood-requests", json=valid_request_payload())

    # Chittagong is ~250 km from the Dhaka hospital.
    r = donor_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 22.3569, "longitude": 91.7832, "radius_km": 50},
    )
    assert r.json()["total"] == 0


def test_nearby_drops_a_request_once_it_is_accepted(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    r = donor_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 23.7925, "longitude": 90.4078},
    )
    assert r.json()["total"] == 0


def test_nearby_rejects_impossible_coordinates(donor_client):
    r = donor_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 200, "longitude": 90.4078},
    )
    assert r.status_code == 422


def test_past_needed_date_is_rejected(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests",
        json=valid_request_payload(
            needed_date=str(business_today() - timedelta(days=1))
        ),
    )
    assert r.status_code == 422


def test_today_needed_date_is_accepted(recipient_client):
    r = recipient_client.post(
        "/api/v1/blood-requests",
        json=valid_request_payload(needed_date=str(business_today())),
    )
    assert r.status_code == 201, r.text


def test_stale_pending_request_is_expired_and_hidden(
    donor_client, session, sample_user
):
    from app.db.models import BloodRequest

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Old request",
        blood_group=BloodGroup.O_POS.value,
        units=1,
        hospital_name="Old Hospital",
        latitude=23.75,
        longitude=90.39,
        needed_date=business_today() - timedelta(days=1),
        contact_number="+8801700000999",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    response = donor_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 23.79, "longitude": 90.40},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0
    session.refresh(req)
    assert req.status == RequestStatus.EXPIRED.value


def test_expired_request_cannot_be_accepted(donor_client, session, sample_user):
    from app.db.models import BloodRequest

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Old request",
        blood_group=BloodGroup.O_POS.value,
        units=1,
        hospital_name="Old Hospital",
        needed_date=business_today() - timedelta(days=1),
        contact_number="+8801700000999",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    response = donor_client.post(f"/api/v1/blood-requests/{req.id}/accept")
    assert response.status_code == 400
    session.refresh(req)
    assert req.status == RequestStatus.EXPIRED.value
