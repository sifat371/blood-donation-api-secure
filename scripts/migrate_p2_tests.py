"""One-time test migration from P1 single-donor semantics to P2 commitments."""

from pathlib import Path
import re


def replace_function(path: str, name: str, replacement: str) -> None:
    file_path = Path(path)
    text = file_path.read_text()
    pattern = re.compile(
        rf"(?ms)^def {re.escape(name)}\(.*?(?=^def |\Z)"
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {name} in {path}, found {len(matches)}")
    updated = pattern.sub(replacement.rstrip() + "\n\n", text, count=1)
    file_path.write_text(updated)


# REST lifecycle expectations.
replace_function(
    "tests/test_api_blood_requests.py",
    "test_donor_can_accept_pending_request",
    '''def test_donor_can_accept_pending_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == RequestStatus.PARTIALLY_COMMITTED.value
    assert body["accepted_by"] is not None
    assert body["units_committed"] == 1
    assert body["remaining_units"] == 1
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_recipient_can_complete_accepted_request",
    '''def test_recipient_can_complete_accepted_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=1)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    commitment_id = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    ).json()[0]["id"]
    confirmed = recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/commitments/{commitment_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == RequestStatus.COMPLETED.value

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == RequestStatus.COMPLETED.value
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_cannot_complete_a_pending_request",
    '''def test_cannot_complete_a_pending_request(recipient_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/complete")
    assert r.status_code == 409
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_completion_records_donation_history_for_the_donor",
    '''def test_completion_records_donation_history_for_the_donor(
    recipient_client, donor_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=1)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    commitment_id = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    ).json()[0]["id"]
    recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/commitments/{commitment_id}/confirm"
    )

    history = donor_client.get("/api/v1/profile/donation-history").json()
    assert history["total"] == 1
    assert history["items"][0]["hospital"] == "Dhaka Medical College Hospital"
    assert recipient_client.get("/api/v1/profile/donation-history").json()["total"] == 0
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_completion_sets_donor_last_donation_date",
    '''def test_completion_sets_donor_last_donation_date(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=1)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    commitment_id = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    ).json()[0]["id"]
    recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/commitments/{commitment_id}/confirm"
    )

    donor = donor_client.get("/api/v1/profile/me").json()
    assert donor["last_donation_date"] == str(business_today())
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_other_users_cannot_cancel_someone_elses_request",
    '''def test_other_users_cannot_cancel_someone_elses_request(
    recipient_client, donor_client, third_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload()
    ).json()

    assert third_client.post(
        f"/api/v1/blood-requests/{created['id']}/cancel"
    ).status_code == 403

    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    assert donor_client.post(
        f"/api/v1/blood-requests/{created['id']}/cancel"
    ).status_code == 403

    assert recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}"
    ).json()["status"] == RequestStatus.PARTIALLY_COMMITTED.value
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_cannot_cancel_a_completed_request",
    '''def test_cannot_cancel_a_completed_request(recipient_client, donor_client):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=1)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")
    commitment_id = recipient_client.get(
        f"/api/v1/blood-requests/{created['id']}/commitments"
    ).json()[0]["id"]
    recipient_client.post(
        f"/api/v1/blood-requests/{created['id']}/commitments/{commitment_id}/confirm"
    )

    r = recipient_client.post(f"/api/v1/blood-requests/{created['id']}/cancel")
    assert r.status_code == 400
''',
)

replace_function(
    "tests/test_api_blood_requests.py",
    "test_nearby_drops_a_request_once_it_is_accepted",
    '''def test_nearby_keeps_partially_committed_request_with_open_capacity(
    recipient_client, donor_client
):
    created = recipient_client.post(
        "/api/v1/blood-requests", json=valid_request_payload(units=2)
    ).json()
    donor_client.post(f"/api/v1/blood-requests/{created['id']}/accept")

    r = donor_client.get(
        "/api/v1/blood-requests/nearby",
        params={"latitude": 23.7925, "longitude": 90.4078},
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["status"] == RequestStatus.PARTIALLY_COMMITTED.value
''',
)

# Lifecycle notification now fires when a commitment is confirmed.
replace_function(
    "tests/test_api_notifications.py",
    "test_completing_a_request_notifies_the_donor",
    '''def test_completing_a_request_notifies_the_donor(recipient_client, donor_client):
    request_id = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=1)
    ).json()["id"]
    donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    commitment_id = recipient_client.get(
        f"{API}/blood-requests/{request_id}/commitments"
    ).json()[0]["id"]
    assert recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/confirm"
    ).status_code == 200

    donor_alerts = donor_client.get(f"{API}/notifications").json()["items"]
    completed = [
        n for n in donor_alerts if n["type"] == NotificationType.REQUEST_COMPLETED.value
    ]
    assert completed, donor_alerts
    assert json.loads(completed[0]["data"])["request_id"] == request_id
''',
)

# Unit/service tests use the new aggregate rather than removed accepted_by.
replace_function(
    "tests/test_blood_requests.py",
    "test_blood_request_lifecycle",
    '''def test_blood_request_lifecycle(session, sample_user, donor_user):
    from sqlmodel import select
    from app.db.models import DonationCommitment
    from app.services.commitment_service import commit_to_request, confirm_commitment

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Lifecycle Patient",
        blood_group=BloodGroup.O_POS.value,
        units=1,
        hospital_name="Lifecycle Hospital",
        needed_date=business_today(),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()
    session.refresh(req)
    assert req.status == RequestStatus.PENDING.value

    commit_to_request(session, req.id, donor_user)
    session.refresh(req)
    assert req.status == RequestStatus.FULLY_COMMITTED.value
    commitment = session.exec(
        select(DonationCommitment).where(DonationCommitment.request_id == req.id)
    ).one()

    confirm_commitment(session, req.id, commitment.id, sample_user)
    session.refresh(req)
    assert req.status == RequestStatus.COMPLETED.value
''',
)

replace_function(
    "tests/test_blood_requests.py",
    "test_pending_request_claim_is_compare_and_set",
    '''def test_commitment_uses_one_database_unit_slot(session, sample_user, donor_user):
    from sqlmodel import select
    from app.db.models import DonationCommitment
    from app.services.commitment_service import commit_to_request

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Atomic Slot",
        blood_group="O+",
        units=1,
        hospital_name="Test Hospital",
        needed_date=business_today(),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    commit_to_request(session, req.id, donor_user)
    commit_to_request(session, req.id, donor_user)
    commitments = session.exec(
        select(DonationCommitment).where(DonationCommitment.request_id == req.id)
    ).all()
    assert len(commitments) == 1
    assert commitments[0].slot_number == 1
''',
)

replace_function(
    "tests/test_blood_requests.py",
    "test_accept_lifecycle_uses_one_database_commit",
    '''def test_accept_lifecycle_uses_one_database_commit(
    session, sample_user, donor_user, monkeypatch
):
    from app.services.request_service import accept_request

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="One Commit",
        blood_group="O+",
        units=1,
        hospital_name="Test Hospital",
        needed_date=business_today(),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    real_commit = session.commit
    commits = 0

    def counting_commit():
        nonlocal commits
        commits += 1
        return real_commit()

    monkeypatch.setattr(session, "commit", counting_commit)
    accepted = accept_request(session, req.id, donor_user)
    assert accepted.status == RequestStatus.FULLY_COMMITTED.value
    assert commits == 1
''',
)

# Keep the end-to-end test two-user: request one unit, then confirm that one
# commitment explicitly before using the compatibility /complete endpoint.
replace_function(
    "tests/test_e2e_multi_user.py",
    "test_full_requester_and_donor_workflow",
    '''def test_full_requester_and_donor_workflow(
    recipient_client, donor_client, sample_user, donor_user, session
):
    """Complete P2 happy path with one requested unit and two accounts."""
    me_recipient = recipient_client.get(f"{API}/profile/me")
    me_donor = donor_client.get(f"{API}/profile/me")
    assert me_recipient.status_code == 200
    assert me_donor.status_code == 200
    assert me_recipient.json()["email"] == "test@example.com"
    assert me_donor.json()["email"] == "donor@example.com"
    assert me_recipient.json()["id"] != me_donor.json()["id"]

    created = recipient_client.post(
        f"{API}/blood-requests", json=valid_request_payload(units=1)
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["units"] == 1
    assert created.json()["units_required"] == 1
    assert created.json()["blood_group"] == BloodGroup.O_POS.value
    assert created.json()["status"] == "Pending"
    assert created.json()["recipient_id"] == sample_user.id
    assert created.json()["accepted_by"] is None

    mine = recipient_client.get(f"{API}/blood-requests/mine")
    assert mine.status_code == 200
    assert [r["id"] for r in mine.json()["items"]] == [request_id]

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

    nearby = donor_client.get(
        f"{API}/blood-requests/nearby",
        params={"latitude": 23.7925, "longitude": 90.4078, "radius_km": 50},
    )
    assert nearby.status_code == 200
    feed = {r["id"]: r for r in nearby.json()["items"]}
    assert request_id in feed
    assert feed[request_id]["distance_km"] > 0

    detail = donor_client.get(f"{API}/blood-requests/{request_id}")
    assert detail.status_code == 200
    assert detail.json()["donor_phone"] is None

    accepted = donor_client.post(f"{API}/blood-requests/{request_id}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "Fully Committed"
    assert accepted.json()["accepted_by"] == donor_user.id
    assert accepted.json()["units_committed"] == 1

    after_accept = recipient_client.get(f"{API}/blood-requests/{request_id}")
    assert after_accept.status_code == 200
    assert after_accept.json()["status"] == "Fully Committed"
    assert after_accept.json()["donor_name"] == donor_user.name
    assert after_accept.json()["donor_phone"] == donor_user.phone

    alerts = recipient_client.get(f"{API}/notifications")
    assert alerts.status_code == 200
    accept_alerts = [
        n for n in alerts.json()["items"] if "accept" in n["title"].lower()
    ]
    assert accept_alerts, alerts.json()
    assert str(request_id) in str(accept_alerts[0]["data"])

    commitments = recipient_client.get(
        f"{API}/blood-requests/{request_id}/commitments"
    )
    assert commitments.status_code == 200
    commitment_id = commitments.json()[0]["id"]
    confirmed = recipient_client.post(
        f"{API}/blood-requests/{request_id}/commitments/{commitment_id}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "Completed"
    assert confirmed.json()["units_completed"] == 1

    completed = recipient_client.post(f"{API}/blood-requests/{request_id}/complete")
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "Completed"

    history = donor_client.get(f"{API}/profile/donation-history")
    assert history.status_code == 200
    assert history.json()["total"] == 1

    donor_profile = donor_client.get(f"{API}/profile/me").json()
    assert donor_profile["last_donation_date"] == str(business_today())

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
''',
)

print("P2 lifecycle test migration complete")
