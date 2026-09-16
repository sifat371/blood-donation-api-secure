"""P2 MCP contract for donor commitments.

These tests keep the chat/tool path on the same transactional service as REST.
Model-supplied identity is never trusted.
"""

from sqlmodel import select

from app.core.time import business_today
from app.db.models import (
    BloodGroup,
    DonationCommitment,
    DonationHistory,
    RequestStatus,
    User,
)
from app.mcp_tools.tools import dispatch_tool


def _payload(**overrides) -> dict:
    payload = {
        "patient_name": "MCP P2 Patient",
        "blood_group": BloodGroup.O_POS.value,
        "units": 2,
        "hospital_name": "MCP Hospital",
        "needed_date": str(business_today()),
        "contact_number": "+8801711111111",
        "latitude": 23.7261,
        "longitude": 90.3960,
    }
    payload.update(overrides)
    return payload


def _second_donor(session) -> User:
    donor = User(
        name="MCP Donor Two",
        email="mcp-donor-two@example.com",
        email_verified=True,
        phone="+8801800000022",
        blood_group=BloodGroup.O_POS.value,
        is_available=True,
        gender="Male",
        latitude=23.78,
        longitude=90.40,
    )
    session.add(donor)
    session.commit()
    session.refresh(donor)
    return donor


def _commitment(session, request_id: int, donor_id: int) -> DonationCommitment:
    return session.exec(
        select(DonationCommitment).where(
            DonationCommitment.request_id == request_id,
            DonationCommitment.donor_id == donor_id,
        )
    ).one()


def test_update_request_accept_and_withdraw_follow_commitment_state(
    session, sample_user, donor_user
):
    created = dispatch_tool(session, sample_user, "CreateBloodRequest", _payload())
    request_id = created["id"]

    accepted = dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "accept"},
    )
    assert accepted["status"] == RequestStatus.PARTIALLY_COMMITTED.value
    assert accepted["units_required"] == 2
    assert accepted["units_committed"] == 1
    assert accepted["units_completed"] == 0
    assert accepted["remaining_units"] == 1

    withdrawn = dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "withdraw"},
    )
    assert withdrawn["status"] == RequestStatus.PENDING.value
    assert withdrawn["units_committed"] == 0
    assert withdrawn["remaining_units"] == 2


def test_confirm_donation_requires_positive_ids(session, sample_user):
    for args in (
        {"request_id": 0, "commitment_id": 1},
        {"request_id": 1, "commitment_id": 0},
        {"request_id": -1, "commitment_id": 1},
    ):
        result = dispatch_tool(session, sample_user, "ConfirmDonation", args)
        assert "error" in result


def test_confirm_donation_uses_authenticated_recipient_and_is_idempotent(
    session, sample_user, donor_user
):
    created = dispatch_tool(
        session, sample_user, "CreateBloodRequest", _payload(units=1)
    )
    request_id = created["id"]
    dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "accept"},
    )
    commitment = _commitment(session, request_id, donor_user.id)

    spoofed_donor = dispatch_tool(
        session,
        donor_user,
        "ConfirmDonation",
        {
            "request_id": request_id,
            "commitment_id": commitment.id,
            "recipient_id": sample_user.id,
            "user_id": sample_user.id,
        },
    )
    assert "error" in spoofed_donor

    confirmed = dispatch_tool(
        session,
        sample_user,
        "ConfirmDonation",
        {
            "request_id": request_id,
            "commitment_id": commitment.id,
            "donor_id": sample_user.id,
        },
    )
    assert confirmed["status"] == RequestStatus.COMPLETED.value
    assert confirmed["units_completed"] == 1

    again = dispatch_tool(
        session,
        sample_user,
        "ConfirmDonation",
        {"request_id": request_id, "commitment_id": commitment.id},
    )
    assert again["status"] == RequestStatus.COMPLETED.value
    history = session.exec(
        select(DonationHistory).where(DonationHistory.commitment_id == commitment.id)
    ).all()
    assert len(history) == 1


def test_confirm_donation_cannot_confirm_commitment_from_another_request(
    session, sample_user, donor_user
):
    first = dispatch_tool(
        session, sample_user, "CreateBloodRequest", _payload(units=1)
    )
    second = dispatch_tool(
        session,
        sample_user,
        "CreateBloodRequest",
        _payload(units=1, patient_name="Other Request"),
    )
    dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": first["id"], "action": "accept"},
    )
    commitment = _commitment(session, first["id"], donor_user.id)

    result = dispatch_tool(
        session,
        sample_user,
        "ConfirmDonation",
        {"request_id": second["id"], "commitment_id": commitment.id},
    )
    assert "error" in result


def test_mcp_complete_requires_all_units_confirmed(
    session, sample_user, donor_user
):
    created = dispatch_tool(session, sample_user, "CreateBloodRequest", _payload())
    request_id = created["id"]
    donor_two = _second_donor(session)

    for donor in (donor_user, donor_two):
        accepted = dispatch_tool(
            session,
            donor,
            "UpdateRequest",
            {"request_id": request_id, "action": "accept"},
        )
        assert "error" not in accepted

    first = _commitment(session, request_id, donor_user.id)
    dispatch_tool(
        session,
        sample_user,
        "ConfirmDonation",
        {"request_id": request_id, "commitment_id": first.id},
    )

    premature = dispatch_tool(
        session,
        sample_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "complete"},
    )
    assert "error" in premature

    second = _commitment(session, request_id, donor_two.id)
    dispatch_tool(
        session,
        sample_user,
        "ConfirmDonation",
        {"request_id": request_id, "commitment_id": second.id},
    )
    completed = dispatch_tool(
        session,
        sample_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "complete"},
    )
    assert completed["status"] == RequestStatus.COMPLETED.value
    assert completed["units_completed"] == 2


def test_mcp_nearby_returns_open_capacity_counts(session, sample_user, donor_user):
    created = dispatch_tool(session, sample_user, "CreateBloodRequest", _payload())
    request_id = created["id"]
    dispatch_tool(
        session,
        donor_user,
        "UpdateRequest",
        {"request_id": request_id, "action": "accept"},
    )

    results = dispatch_tool(
        session,
        donor_user,
        "GetNearbyRequests",
        {"latitude": 23.75, "longitude": 90.39, "radius_km": 100},
    )
    match = next(item for item in results if item["id"] == request_id)
    assert match["status"] == RequestStatus.PARTIALLY_COMMITTED.value
    assert match["units_required"] == 2
    assert match["units_committed"] == 1
    assert match["units_completed"] == 0
    assert match["remaining_units"] == 1
