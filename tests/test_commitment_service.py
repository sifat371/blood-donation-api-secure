"""Service-level contract for the P2 multi-donor request state machine."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.core.time import business_today
from app.db.models import (
    BloodGroup,
    BloodRequest,
    CommitmentStatus,
    DonationCommitment,
    DonationHistory,
    RequestStatus,
    User,
)
from app.services.commitment_service import (
    commitment_counts,
    commit_to_request,
    confirm_commitment,
    withdraw_commitment,
)


def _request(session, recipient: User, *, units: int = 2) -> BloodRequest:
    request = BloodRequest(
        recipient_id=recipient.id,
        patient_name="P2 Patient",
        blood_group=BloodGroup.O_POS.value,
        units=units,
        hospital_name="P2 Hospital",
        needed_date=business_today() + timedelta(days=1),
        contact_number="+8801712345678",
        status=RequestStatus.PENDING.value,
    )
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def _donor(session, suffix: int) -> User:
    donor = User(
        name=f"P2 Donor {suffix}",
        email=f"p2donor{suffix}@example.com",
        email_verified=True,
        phone=f"+880180000{suffix:04d}",
        blood_group=BloodGroup.O_POS.value,
        is_available=True,
        gender="Male",
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


def test_two_unit_request_tracks_committed_and_completed_separately(
    session, sample_user, donor_user
):
    request = _request(session, sample_user, units=2)
    donor_b = _donor(session, 2)

    commit_to_request(session, request.id, donor_user)
    counts = commitment_counts(session, request.id)
    assert counts.committed == 1
    assert counts.completed == 0
    assert counts.secured == 1
    session.refresh(request)
    assert request.status == RequestStatus.PARTIALLY_COMMITTED.value

    commit_to_request(session, request.id, donor_b)
    counts = commitment_counts(session, request.id)
    assert counts.committed == 2
    assert counts.completed == 0
    assert counts.secured == 2
    session.refresh(request)
    assert request.status == RequestStatus.FULLY_COMMITTED.value

    commitment = _commitment(session, request.id, donor_user.id)
    confirm_commitment(session, request.id, commitment.id, sample_user)

    counts = commitment_counts(session, request.id)
    assert counts.committed == 1
    assert counts.completed == 1
    assert counts.secured == 2
    session.refresh(request)
    assert request.status == RequestStatus.FULLY_COMMITTED.value


def test_request_cannot_be_overbooked(session, sample_user, donor_user):
    request = _request(session, sample_user, units=2)
    donor_b = _donor(session, 3)
    donor_c = _donor(session, 4)

    commit_to_request(session, request.id, donor_user)
    commit_to_request(session, request.id, donor_b)

    with pytest.raises(HTTPException) as exc:
        commit_to_request(session, request.id, donor_c)

    assert exc.value.status_code == 409
    assert commitment_counts(session, request.id).secured == 2


def test_withdrawal_reopens_slot_and_recommit_reuses_same_row(
    session, sample_user, donor_user
):
    request = _request(session, sample_user, units=1)

    commit_to_request(session, request.id, donor_user)
    original = _commitment(session, request.id, donor_user.id)
    original_id = original.id
    assert request.status == RequestStatus.FULLY_COMMITTED.value

    withdraw_commitment(session, request.id, donor_user)
    session.refresh(original)
    session.refresh(request)
    assert original.status == CommitmentStatus.WITHDRAWN.value
    assert request.status == RequestStatus.PENDING.value

    commit_to_request(session, request.id, donor_user)
    reactivated = _commitment(session, request.id, donor_user.id)
    session.refresh(request)
    assert reactivated.id == original_id
    assert reactivated.status == CommitmentStatus.COMMITTED.value
    assert request.status == RequestStatus.FULLY_COMMITTED.value


def test_only_recipient_can_confirm_and_confirmation_is_idempotent(
    session, sample_user, donor_user
):
    request = _request(session, sample_user, units=1)
    commit_to_request(session, request.id, donor_user)
    commitment = _commitment(session, request.id, donor_user.id)

    with pytest.raises(HTTPException) as exc:
        confirm_commitment(session, request.id, commitment.id, donor_user)
    assert exc.value.status_code == 403

    confirmed = confirm_commitment(session, request.id, commitment.id, sample_user)
    assert confirmed.status == RequestStatus.COMPLETED.value
    session.refresh(donor_user)
    assert donor_user.last_donation_date == business_today()

    first_history = session.exec(
        select(DonationHistory).where(DonationHistory.commitment_id == commitment.id)
    ).all()
    assert len(first_history) == 1

    confirm_commitment(session, request.id, commitment.id, sample_user)
    second_history = session.exec(
        select(DonationHistory).where(DonationHistory.commitment_id == commitment.id)
    ).all()
    assert len(second_history) == 1


def test_completed_commitment_cannot_be_withdrawn(session, sample_user, donor_user):
    request = _request(session, sample_user, units=1)
    commit_to_request(session, request.id, donor_user)
    commitment = _commitment(session, request.id, donor_user.id)
    confirm_commitment(session, request.id, commitment.id, sample_user)

    with pytest.raises(HTTPException) as exc:
        withdraw_commitment(session, request.id, donor_user)
    assert exc.value.status_code == 400
