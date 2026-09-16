"""
Tests for blood request services and eligibility.
"""

from datetime import date, timedelta

from app.db.models import BloodRequest, BloodGroup, RequestStatus, User
from app.services.geo import haversine_distance
from app.services.eligibility import is_eligible, days_until_eligible


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
    sample_user.last_donation_date = date.today() - timedelta(days=91)
    sample_user.is_available = True
    assert is_eligible(sample_user) is True


def test_not_eligible_before_90_days(sample_user):
    sample_user.last_donation_date = date.today() - timedelta(days=30)
    sample_user.is_available = True
    assert is_eligible(sample_user) is False


def test_not_eligible_unavailable(sample_user):
    sample_user.is_available = False
    assert is_eligible(sample_user) is False


def test_days_until_eligible_already_eligible(sample_user):
    sample_user.last_donation_date = None
    assert days_until_eligible(sample_user) == 0


def test_days_until_eligible_countdown(sample_user):
    sample_user.last_donation_date = date.today() - timedelta(days=60)
    assert days_until_eligible(sample_user) == 30


# ── Blood request model tests ────────────────────────────


def test_create_blood_request(session, sample_user):
    req = BloodRequest(
        recipient_id=sample_user.id,
        patient_name="Test Patient",
        blood_group=BloodGroup.O_POS.value,
        units=2,
        hospital_name="Test Hospital",
        needed_date=date.today() + timedelta(days=1),
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
        needed_date=date.today(),
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
        needed_date=date.today(),
        contact_number="+8801700000000",
        status=RequestStatus.PENDING.value,
    )
    session.add(req)
    session.commit()

    req.status = RequestStatus.CANCELLED.value
    session.commit()
    session.refresh(req)
    assert req.status == "Cancelled"
