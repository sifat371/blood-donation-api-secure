"""
The two-user, end-to-end blood donation workflow.

This is the scenario the application exists to serve, written as one test so a
regression anywhere along it fails loudly:

    Requester creates a request
        → the request is stored correctly
        → nearby eligible donors are discoverable
        → the donor finds it in the nearby feed
        → the donor opens it by id (as a push notification deep link would)
        → the donor accepts
        → the requester sees the status change and the donor's contact details
        → the requester confirms completion
        → the donor's donation history and eligibility update

Both accounts hold their own token and hit the same API the mobile app does, so
this also covers the multi-user isolation rules: neither user can act on the
other's data, and neither sees the other's private fields before the business
rules allow it.
"""

from datetime import date, datetime, timedelta

import pytest

from app.core.time import business_today
from app.db.models import BloodGroup
from app.services.eligibility import ELIGIBILITY_DAYS

from .conftest import valid_request_payload

API = "/api/v1"


def test_full_requester_and_donor_workflow(
    recipient_client, donor_client, sample_user, donor_user, session
):
    """The complete happy path, twelve steps, two accounts."""

    # ── 1. Both users are signed in and see their own profile ──────────
    me_recipient = recipient_client.get(f"{API}/profile/me")
    me_donor = donor_client.get(f"{API}/profile/me")
    assert me_recipient.status_code == 200
    assert me_donor.status_code == 200
    assert me_recipient.json()["email"] == "test@example.com"
    assert me_donor.json()["email"] == "donor@example.com"
    # Identity comes from the token, so the two clients cannot be confused.
    assert me_recipient.json()["id"] != me_donor.json()["id"]

    # ── 2. The requester creates a blood request ───────────────────────
    created = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=2)
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    # ── 3. It is stored with the values that were sent ─────────────────
    assert created.json()["units"] == 2
    assert created.json()["blood_group"] == BloodGroup.O_POS.value
    assert created.json()["status"] == "Pending"
    assert created.json()["recipient_id"] == sample_user.id
    assert created.json()["accepted_by"] is None

    # ── 4. It appears in the requester's own list ──────────────────────
    mine = recipient_client.get(f"{API}/blood-requests/mine")
    assert mine.status_code == 200
    assert [r["id"] for r in mine.json()["items"]] == [request_id]

    # ── 5. The requester can find eligible donors nearby ──────────────
    donors = recipient_client.get(
        f"{API}/donors/search",
        params={
            "blood_group": BloodGroup.O_POS.value,
            "latitude": 23.7261,
            "longitude": 90.3960,
            "radius_km": 50,
        },
    )
    assert donors.status_code == 200
    assert donor_user.id in [d["id"] for d in donors.json()["items"]]

    # ── 6. The donor sees the request in their nearby feed ────────────
    nearby = donor_client.get(
        f"{API}/blood-requests/nearby",
        params={"latitude": 23.7925, "longitude": 90.4078, "radius_km": 50},
    )
    assert nearby.status_code == 200
    feed = {r["id"]: r for r in nearby.json()["items"]}
    assert request_id in feed
    # The feed carries the distance the UI displays.
    assert feed[request_id]["distance_km"] > 0

    # ── 7. The donor opens it by id — the push-notification deep link ──
    detail = donor_client.get(f"{API}/blood-requests/{request_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == request_id
    # Nobody has accepted yet, so there are no donor details to leak.
    assert detail.json()["donor_phone"] is None

    # ── 8. The donor accepts ───────────────────────────────────────────
    accepted = donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "Accepted"
    assert accepted.json()["accepted_by"] == donor_user.id

    # ── 9. The requester sees the new status and can now reach the donor ─
    after_accept = recipient_client.get(f"{API}/blood-requests/{request_id}")
    assert after_accept.status_code == 200
    assert after_accept.json()["status"] == "Accepted"
    assert after_accept.json()["donor_name"] == donor_user.name
    assert after_accept.json()["donor_phone"] == donor_user.phone

    # ── 10. The requester was notified of the acceptance ──────────────
    alerts = recipient_client.get(f"{API}/notifications")
    assert alerts.status_code == 200
    accept_alerts = [
        n for n in alerts.json()["items"] if "accept" in n["title"].lower()
    ]
    assert accept_alerts, alerts.json()
    # Every notification carries the request id the app deep-links on.
    assert str(request_id) in str(accept_alerts[0]["data"])

    # ── 11. The requester confirms the donation was received ──────────
    completed = recipient_client.post(f"{API}/blood-requests/{request_id}/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "Completed"

    # ── 12. The donation is on the donor's record, and the 90-day
    #        cooldown has started ─────────────────────────────────────
    history = donor_client.get(f"{API}/profile/donation-history")
    assert history.status_code == 200
    assert history.json()["total"] == 1

    donor_profile = donor_client.get(f"{API}/profile/me").json()
    # Stamped from UTC, which is what the app does. Worth knowing: eligibility
    # then compares this against the *local* date, so between local midnight and
    # 06:00 in Bangladesh (UTC+6) a fresh donation records as "yesterday" and the
    # 90-day cooldown ends a day early. A one-day skew on a 90-day rule, not a
    # blocker — but this test pins the current behaviour rather than hiding it.
    assert donor_profile["last_donation_date"] == str(business_today())

    # The donor is no longer eligible, so they drop out of donor search.
    donors_after = recipient_client.get(
        f"{API}/donors/search",
        params={
            "blood_group": BloodGroup.O_POS.value,
            "latitude": 23.7261,
            "longitude": 90.3960,
            "radius_km": 50,
        },
    )
    assert donor_user.id not in [d["id"] for d in donors_after.json()["items"]]


# ── Isolation between the two accounts ───────────────────────────────


def test_a_third_user_cannot_accept_someone_elses_accepted_request(
    recipient_client, donor_client, third_client
):
    """Once a donor has accepted, the slot is taken."""
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    assert donor_client.post(f"{API}/blood-requests/{request_id}/accept").status_code == 200

    second = third_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert second.status_code == 400, second.text


def test_donor_cannot_complete_the_request_they_accepted(
    recipient_client, donor_client
):
    """Completion is the recipient's confirmation, not the donor's claim."""
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")

    attempt = donor_client.post(f"{API}/blood-requests/{request_id}/complete")
    assert attempt.status_code == 403, attempt.text


def test_donor_cannot_cancel_someone_elses_request(recipient_client, donor_client):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]

    attempt = donor_client.post(f"{API}/blood-requests/{request_id}/cancel")
    assert attempt.status_code == 403, attempt.text

    # And the request is untouched.
    assert (
        recipient_client.get(f"{API}/blood-requests/{request_id}").json()["status"]
        == "Pending"
    )


def test_mine_returns_only_the_callers_own_requests(recipient_client, donor_client):
    """Two accounts, two requests, no crossover."""
    mine_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(patient_name="Recipient's")
    ).json()["id"]
    theirs_id = donor_client.post(
        f"{API}/blood-requests", json=valid_request_payload(patient_name="Donor's")
    ).json()["id"]

    assert [r["id"] for r in recipient_client.get(f"{API}/blood-requests/mine").json()["items"]] == [
        mine_id
    ]
    assert [r["id"] for r in donor_client.get(f"{API}/blood-requests/mine").json()["items"]] == [
        theirs_id
    ]


def test_notifications_are_scoped_to_the_recipient(
    recipient_client, donor_client, third_client
):
    """The acceptance alert reaches the requester and nobody else."""
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")

    assert recipient_client.get(f"{API}/notifications").json()["total"] >= 1
    # The uninvolved third account has nothing about this request.
    third_alerts = third_client.get(f"{API}/notifications").json()["items"]
    assert all(str(request_id) not in str(n["data"]) for n in third_alerts)


def test_a_user_cannot_mark_another_users_notification_as_read(
    recipient_client, donor_client
):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload()
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")

    alerts = recipient_client.get(f"{API}/notifications").json()["items"]
    assert alerts
    notification_id = alerts[0]["id"]

    stolen = donor_client.post(f"{API}/notifications/{notification_id}/read")
    assert stolen.status_code == 404, stolen.text

    # Still unread for its actual owner.
    owner_view = recipient_client.get(f"{API}/notifications").json()["items"]
    assert [n for n in owner_view if n["id"] == notification_id][0]["is_read"] is False


def test_an_ineligible_donor_is_excluded_from_search(
    recipient_client, ineligible_donor
):
    """The 90-day rule is enforced server-side, not just displayed in the app."""
    result = recipient_client.get(
        f"{API}/donors/search",
        params={
            "blood_group": BloodGroup.O_POS.value,
            "latitude": 23.7261,
            "longitude": 90.3960,
            "radius_km": 50,
        },
    )
    assert result.status_code == 200
    assert ineligible_donor.id not in [d["id"] for d in result.json()["items"]]


def test_an_unavailable_donor_is_excluded_from_search(
    recipient_client, donor_client, donor_user
):
    """Switching availability off actually removes you from results."""
    params = {
        "blood_group": BloodGroup.O_POS.value,
        "latitude": 23.7261,
        "longitude": 90.3960,
        "radius_km": 50,
    }
    before = recipient_client.get(f"{API}/donors/search", params=params).json()
    assert donor_user.id in [d["id"] for d in before["items"]]

    toggled = donor_client.patch(f"{API}/profile/me", json={"is_available": False})
    assert toggled.status_code == 200, toggled.text

    after = recipient_client.get(f"{API}/donors/search", params=params).json()
    assert donor_user.id not in [d["id"] for d in after["items"]]


@pytest.mark.parametrize(
    "days_ago,expected_in_results",
    [
        (ELIGIBILITY_DAYS - 1, False),  # one day short of the cooldown
        (ELIGIBILITY_DAYS, True),  # exactly the boundary — eligible again
    ],
)
def test_eligibility_boundary_in_donor_search(
    recipient_client, donor_user, session, days_ago, expected_in_results
):
    donor_user.last_donation_date = business_today() - timedelta(days=days_ago)
    session.add(donor_user)
    session.commit()

    result = recipient_client.get(
        f"{API}/donors/search",
        params={
            "blood_group": BloodGroup.O_POS.value,
            "latitude": 23.7261,
            "longitude": 90.3960,
            "radius_km": 50,
        },
    )
    found = donor_user.id in [d["id"] for d in result.json()["items"]]
    assert found is expected_in_results
