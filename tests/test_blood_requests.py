"""
Tests for blood request services and eligibility.
"""

from datetime import date, timedelta

from app.db.models import BloodRequest, BloodGroup, RequestStatus, User
from app.services.geo import haversine_distance
from app.services.eligibility import is_eligible, days_until_eligible
from app.core.time import business_today


# ── Geo tests ─────────────────────────────────────────────


def test_haversine_same_point():
    d = haversine_distance(23.8103, 90.4125, 23.8103, 90.4125)
    assert d == 0.0


def test_haversine_dhaka_to_chittagong():
    # Dhaka → Chittagong ≈ 250 km
    d = haversine_distance(23.8103, 90.4125, 22.3569, 91.7832)
    assert 200 < d < 300


def test_haversine_short_distance():
    # Dhanmondi → Gulshan ≈ 6-8 km
    d = haversine_distance(23.7461, 90.3742, 23.7925, 90.4078)
    assert 4 < d < 10


# ── Eligibility tests ────────────────────────────────────


def test_eligible_no_donation(sample_user):
    sample_user.last_donation_date = None
    sample_user.is_available = True
    assert is_eligible(sample_user) is True


def test_eligible_after_90_days(sample_user):
    sample_user.last_donation_date = business_today() - timedelta(days=91)
    sample_user.is_available = True
    assert is_eligible(sample_user) is True


def test_not_eligible_before_90_days(sample_user):
    sample_user.last_donation_date = business_today() - timedelta(days=30)
    sample_user.is_available = True
    assert is_eligible(sample_user) is False


def test_not_eligible_unavailable(sample_user):
    sample_user.is_available = False
    assert is_eligible(sample_user) is False


def test_days_until_eligible_already_eligible(sample_user):
    sample_user.last_donation_date = None
    assert days_until_eligible(sample_user) == 0


def test_days_until_eligible_countdown(sample_user):
    sample_user.last_donation_date = business_today() - timedelta(days=60)
    assert days_until_eligible(sample_user) == 30


# ── Blood request model tests ────────────────────────────


def test_create_blood_request(session, sample_user):
    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Test Patient",
        blood_group=BloodGroup.O_POS.value,
        units=2,
        hospital_name="Test Hospital",
        needed_date=business_today() + timedelta(days=1),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
        latitude=23.7461,
        longitude=90.3742,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    assert req.id is not None
    assert req.status == "Pending"
    assert req.blood_group == "O+"


def test_blood_request_lifecycle(session, sample_user, donor_user):
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

def test_blood_request_cancel(session, sample_user):
    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Cancel Patient",
        blood_group=BloodGroup.B_NEG.value,
        units=1,
        hospital_name="Cancel Hospital",
        needed_date=business_today(),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()

    req.status = RequestStatus.CANCELLED.value
    session.commit()
    session.refresh(req)
    assert req.status == "Cancelled"


def test_commitment_uses_one_database_unit_slot(session, sample_user, donor_user):
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

def test_accept_lifecycle_uses_one_database_commit(
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

def test_expired_request_never_fans_out_notifications(
    session, sample_user, donor_user
):
    from sqlmodel import select
    from app.core.time import business_today
    from app.db.models import BloodRequest, Notification, RequestStatus
    from app.services.request_service import notify_nearby_donors

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Expired Fanout",
        blood_group="O+",
        units=1,
        hospital_name="Test Hospital",
        latitude=23.75,
        longitude=90.39,
        needed_date=business_today() - timedelta(days=1),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()
    session.refresh(req)

    notify_nearby_donors(session, req)
    session.refresh(req)
    assert req.status == RequestStatus.EXPIRED.value
    assert session.exec(select(Notification)).all() == []
