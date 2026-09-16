"""One-shot patch for two legacy MCP tests after P2 commitment semantics."""

from pathlib import Path
import re

PATH = Path("tests/test_mcp_tools.py")
text = PATH.read_text()


def replace(name: str, body: str) -> None:
    global text
    pattern = re.compile(rf"(?ms)^def {re.escape(name)}\(.*?(?=^def |^@pytest|\Z)")
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {name}; found {len(matches)}")
    text = pattern.sub(body.rstrip() + "\n\n", text, count=1)


replace(
    "test_update_request_cannot_complete_as_the_donor",
    '''def test_update_request_cannot_complete_as_the_donor(session, sample_user, donor_user):
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
''',
)

replace(
    "test_nearby_requests_caps_ai_results",
    '''def test_nearby_requests_caps_ai_results(session, sample_user, donor_user):
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
''',
)

PATH.write_text(text)
