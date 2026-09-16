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
    # Create
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
    assert req.status == "Pending"

    # Accept
    req.accepted_by = donor_user.id
    req.status = RequestStatus.ACCEPTED.value
    session.commit()
    session.refresh(req)
    assert req.status == "Accepted"
    assert req.accepted_by == donor_user.id

    # Complete
    req.status = RequestStatus.COMPLETED.value
    session.commit()
    session.refresh(req)
    assert req.status == "Completed"


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


def test_pending_request_claim_is_compare_and_set(session, sample_user, donor_user):
    from app.core.time import business_today, utc_now
    from app.db.models import BloodRequest, RequestStatus
    from app.services.request_service import _claim_pending_request

    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Atomic Claim",
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

    assert _claim_pending_request(session, req.id, donor_user.id, utc_now()) is True
    assert _claim_pending_request(session, req.id, donor_user.id, utc_now()) is False
    session.rollback()


def test_accept_lifecycle_uses_one_database_commit(
    session, sample_user, donor_user, monkeypatch
):
    from app.core.time import business_today
    from app.db.models import BloodRequest, RequestStatus
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
    assert accepted.status == RequestStatus.ACCEPTED.value
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
