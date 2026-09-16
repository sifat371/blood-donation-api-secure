"""
MCP tool layer — authorisation and correctness.

These tools are reachable from chat, which means their arguments come from a
language model steered by user text. The invariant under test: a tool acts as,
and only as, the authenticated user handed to `dispatch_tool`, no matter what
the arguments say. A prompt-injected `user_id` must never redirect a read or a
write to another account.
"""

from datetime import date, timedelta

import pytest

from app.db.models import BloodGroup, RequestStatus
from app.core.time import business_today
from app.mcp_tools.tools import (
    TOOL_DEFINITIONS,
    TOOL_MAP,
    _FORBIDDEN_ARGS,
    dispatch_tool,
)


def _payload(**overrides) -> dict:
    args = {
        "patient_name": "Karim Rahman",
        "blood_group": BloodGroup.O_POS.value,
        "units": 2,
        "hospital_name": "Dhaka Medical College Hospital",
        "needed_date": str(business_today() + timedelta(days=1)),
        "contact_number": "+8801711111111",
        "latitude": 23.7261,
        "longitude": 90.3960,
    }
    args.update(overrides)
    return args


# ── Registry sanity ──────────────────────────────────────


def test_every_declared_tool_is_implemented():
    declared = {t["name"] for t in TOOL_DEFINITIONS}
    assert declared == set(TOOL_MAP), declared.symmetric_difference(TOOL_MAP)


def test_unknown_tool_is_reported_not_raised():
    """A hallucinated tool name must not break the chat turn."""
    result = dispatch_tool(None, None, "DropAllTables", {})
    assert "error" in result


def test_non_dict_arguments_are_rejected(session, sample_user):
    result = dispatch_tool(session, sample_user, "CheckEligibility", "not a dict")
    assert "error" in result


def test_bad_arguments_return_an_error_rather_than_raising(session, sample_user):
    """Wrong argument names from the model shouldn't 500 the request."""
    result = dispatch_tool(
        session, sample_user, "CheckEligibility", {"nonexistent_arg": 1}
    )
    assert "error" in result


# ── Identity cannot be overridden ────────────────────────


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_ARGS))
def test_identity_arguments_are_stripped_from_every_tool(
    session, sample_user, donor_user, forbidden
):
    """
    Passing another account's id alongside a legitimate call is ignored.

    Checked against GetUserProfile because its return value names whose profile
    was actually read, so a successful override would be unmistakable.
    """
    result = dispatch_tool(
        session,
        sample_user,
        "GetUserProfile",
        {forbidden: donor_user.id},
    )
    assert result.get("email") == sample_user.email
    assert result.get("email") != donor_user.email


def test_get_donation_history_reads_only_the_callers_records(
    session, sample_user, donor_user
):
    """A donation belonging to one user is invisible to the other."""
    from app.db.models import DonationHistory

    session.add(
        DonationHistory(
            donor_id=donor_user.id,
            blood_group=donor_user.blood_group,
            date=business_today(),
            status="Completed",
        )
    )
    session.commit()

    # Note the shape: the tool layer returns a bare list, not the paginated
    # {"items": [...], "total": n} envelope the REST endpoints use.
    as_donor = dispatch_tool(session, donor_user, "GetDonationHistory", {})
    as_other = dispatch_tool(session, sample_user, "GetDonationHistory", {})

    assert len(as_donor) == 1
    assert as_other == []

    # And asking for the donor's id while authenticated as someone else changes
    # nothing.
    spoofed = dispatch_tool(
        session, sample_user, "GetDonationHistory", {"user_id": donor_user.id}
    )
    assert spoofed == []


def test_get_notifications_reads_only_the_callers_notifications(
    session, sample_user, donor_user
):
    from app.services.notifications import create_notification
    from app.db.models import NotificationType

    create_notification(
        session,
        user_id=donor_user.id,
        notification_type=NotificationType.NEW_BLOOD_REQUEST,
        title="For the donor only",
        body="private",
        data={"request_id": 1},
    )
    session.commit()

    theirs = dispatch_tool(session, donor_user, "GetNotifications", {})
    mine = dispatch_tool(session, sample_user, "GetNotifications", {})

    assert any(n["title"] == "For the donor only" for n in theirs)
    assert mine == []

    spoofed = dispatch_tool(
        session, sample_user, "GetNotifications", {"user_id": donor_user.id}
    )
    assert spoofed == []


def test_update_availability_writes_only_to_the_caller(
    session, sample_user, donor_user
):
    """The classic IDOR: turning someone else's availability off."""
    assert donor_user.is_available is True

    dispatch_tool(
        session,
        sample_user,
        "UpdateAvailability",
        {"is_available": False, "user_id": donor_user.id},
    )
    session.refresh(donor_user)
    session.refresh(sample_user)

    # The caller changed; the target did not.
    assert sample_user.is_available is False
    assert donor_user.is_available is True


def test_update_user_location_writes_only_to_the_caller(
    session, sample_user, donor_user
):
    original_donor_lat = donor_user.latitude

    dispatch_tool(
        session,
        sample_user,
        "UpdateUserLocation",
        {"latitude": 24.0, "longitude": 91.0, "user_id": donor_user.id},
    )
    session.refresh(donor_user)
    session.refresh(sample_user)

    assert sample_user.latitude == 24.0
    assert sample_user.longitude == 91.0
    assert donor_user.latitude == original_donor_lat


def test_created_requests_belong_to_the_caller(session, sample_user, donor_user):
    """A `recipient_id` in the arguments must not reassign ownership."""
    from app.db.models import BloodRequest

    result = dispatch_tool(
        session,
        sample_user,
        "CreateBloodRequest",
        _payload(recipient_id=donor_user.id),
    )
    assert "error" not in result, result

    # The tool's return value doesn't name the owner, so check the stored row.
    stored = session.get(BloodRequest, result["id"])
    assert stored.recipient_id == sample_user.id
    assert stored.recipient_id != donor_user.id


def test_check_eligibility_reports_on_the_caller(
    session, sample_user, ineligible_donor
):
    mine = dispatch_tool(session, sample_user, "CheckEligibility", {})
    theirs = dispatch_tool(session, ineligible_donor, "CheckEligibility", {})

    assert mine["is_eligible"] is True
    assert theirs["is_eligible"] is False
    assert theirs["days_until_eligible"] > 0

    # Asking about the ineligible donor while authenticated as an eligible user
    # answers for the caller.
    spoofed = dispatch_tool(
        session, sample_user, "CheckEligibility", {"user_id": ineligible_donor.id}
    )
    assert spoofed["is_eligible"] is True
    assert spoofed["user_id"] == sample_user.id


# ── UpdateRequest enforces the same rules as the REST API ─


def test_update_request_cannot_cancel_another_users_request(
    session, sample_user, donor_user
):
    created = dispatch_tool(session, sample_user, "CreateBloodRequest", _payload())
    request_id = created["id"]

    result = dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "cancel"},
    )
    assert "error" in result, result

    from app.db.models import BloodRequest

    assert session.get(BloodRequest, request_id).status == RequestStatus.PENDING.value


def test_update_request_cannot_complete_as_the_donor(session, sample_user, donor_user):
    from sqlmodel import select
    from app.db.models import DonationCommitment

    created = dispatch_tool(
        session, sample_user, "CreateBloodRequest", _payload(units=1)
    )
    request_id = created["id"]

    accepted = dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "accept"},
    )
    assert "error" not in accepted, accepted

    as_donor = dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "complete"},
    )
    assert "error" in as_donor, as_donor

    commitment = session.exec(
        select(DonationCommitment).where(
            DonationCommitment.request_id == request_id,
            DonationCommitment.donor_id == donor_user.id,
        )
    ).one()
    confirmed = dispatch_tool(
        session,
        sample_user,
        "ConfirmDonation",
        {"request_id": request_id, "commitment_id": commitment.id},
    )
    assert confirmed["status"] == RequestStatus.COMPLETED.value

    as_recipient = dispatch_tool(
        session,
        sample_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "complete"},
    )
    assert "error" not in as_recipient, as_recipient
    assert as_recipient["status"] == RequestStatus.COMPLETED.value

def test_update_request_rejects_an_unknown_action(session, sample_user):
    created = dispatch_tool(session, sample_user, "CreateBloodRequest", _payload())
    result = dispatch_tool(
        session,
        sample_user,
        "UpdateRequest",
        {"request_id": created["id"], "action": "delete"},
    )
    assert "error" in result


def test_update_request_on_a_missing_request_is_an_error_not_a_crash(
    session, sample_user
):
    result = dispatch_tool(
        session, sample_user, "UpdateRequest", {"request_id": 999999, "action": "accept"}
    )
    assert "error" in result


# ── The units rule holds through the chat path too ───────


@pytest.mark.parametrize("units", [0, -1, 11, 50])
def test_invalid_units_are_rejected_via_the_tool_layer(session, sample_user, units):
    """
    The same business rule as POST /blood-requests, reached a different way.

    A chat request that asks for 50 units must fail cleanly rather than writing
    a row the REST API would have refused.
    """
    result = dispatch_tool(
        session, sample_user, "CreateBloodRequest", _payload(units=units)
    )
    assert "error" in result, result


@pytest.mark.parametrize("units", [1, 5, 10])
def test_valid_units_are_accepted_via_the_tool_layer(session, sample_user, units):
    result = dispatch_tool(
        session, sample_user, "CreateBloodRequest", _payload(units=units)
    )
    assert "error" not in result, result
    assert result["units"] == units


# ── Donor search through the tool layer ──────────────────


def test_find_donors_excludes_the_caller(session, sample_user, donor_user):
    """You are not your own donor match."""
    result = dispatch_tool(
        session,
        sample_user,
        "FindDonors",
        {
            "blood_group": BloodGroup.O_POS.value,
            "latitude": 23.7461,
            "longitude": 90.3742,
            "radius_km": 50,
        },
    )
    ids = [d["id"] for d in result]
    assert donor_user.id in ids
    assert sample_user.id not in ids
    match = next(d for d in result if d["id"] == donor_user.id)
    assert "phone" not in match


def test_find_donors_excludes_ineligible_donors(
    session, sample_user, ineligible_donor
):
    result = dispatch_tool(
        session,
        sample_user,
        "FindDonors",
        {
            "blood_group": BloodGroup.O_POS.value,
            "latitude": 23.7461,
            "longitude": 90.3742,
            "radius_km": 50,
        },
    )
    assert ineligible_donor.id not in [d["id"] for d in result]


@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("FindDonors", {"blood_group": "Z+", "latitude": 23.7, "longitude": 90.4}),
        ("FindDonors", {"blood_group": "O+", "latitude": 91, "longitude": 90.4}),
        ("FindDonors", {"blood_group": "O+", "latitude": 23.7, "longitude": 90.4, "radius_km": 101}),
        ("GetNearbyRequests", {"latitude": -91, "longitude": 90.4}),
        ("UpdateAvailability", {"is_available": "false"}),
        ("UpdateUserLocation", {"latitude": 23.7, "longitude": 181}),
    ],
)
def test_untrusted_tool_arguments_are_strictly_validated(
    session, sample_user, tool_name, args
):
    result = dispatch_tool(session, sample_user, tool_name, args)
    assert isinstance(result, dict) and "error" in result


def test_unknown_non_identity_tool_argument_is_rejected(session, sample_user):
    result = dispatch_tool(
        session, sample_user, "CheckEligibility", {"surprise": "value"}
    )
    assert "error" in result


def test_find_donors_caps_ai_results(session, sample_user):
    from app.db.models import User

    for index in range(25):
        session.add(
            User(
                name=f"Bulk Donor {index}",
                email=f"bulk{index}@example.com",
                email_verified=True,
                phone=f"+880180000{index:04d}",
                blood_group=BloodGroup.O_POS.value,
                latitude=23.75 + index * 0.0001,
                longitude=90.39,
                is_available=True,
            )
        )
    session.commit()

    result = dispatch_tool(
        session,
        sample_user,
        "FindDonors",
        {
            "blood_group": "O+",
            "latitude": 23.75,
            "longitude": 90.39,
            "radius_km": 100,
        },
    )
    assert len(result) == 20


def test_nearby_requests_caps_ai_results(session, sample_user, donor_user):
    from app.db.models import BloodRequest
    from app.core.time import business_today

    for index in range(25):
        session.add(
            BloodRequest(
                recipient_id=sample_user.id,
                patient_name=f"Patient {index}",
                blood_group=BloodGroup.O_POS.value,
                units=1,
                hospital_name=f"Hospital {index}",
                latitude=23.75 + index * 0.0001,
                longitude=90.39,
                needed_date=business_today(),
                contact_number="+8801700000999",
                status=RequestStatus.PENDING.value,
            )
        )
    session.commit()

    result = dispatch_tool(
        session,
        donor_user,
        "GetNearbyRequests",
        {"latitude": 23.75, "longitude": 90.39, "radius_km": 100},
    )
    assert len(result) == 20

