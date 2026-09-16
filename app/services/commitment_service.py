"""Transactional P2 donor-commitment state machine.

One donor owns one unit slot. Database uniqueness on (request_id, slot_number)
provides the final overbooking guard; PostgreSQL additionally locks the parent
request while allocating a slot.
"""

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app.core.time import business_today, utc_now
from app.db.models import (
    BloodRequest,
    CommitmentStatus,
    DonationCommitment,
    DonationHistory,
    NotificationType,
    RequestStatus,
    User,
)
from app.services.auth_service import audit_log
from app.services.eligibility import is_blood_compatible, is_eligible
from app.services.notifications import create_notification, send_notification_push

_ACTIVE_COMMITMENT_STATUSES = (
    CommitmentStatus.COMMITTED.value,
    CommitmentStatus.COMPLETED.value,
)
_TERMINAL_REQUEST_STATUSES = (
    RequestStatus.COMPLETED.value,
    RequestStatus.CANCELLED.value,
    RequestStatus.EXPIRED.value,
)


@dataclass(frozen=True)
class CommitmentCounts:
    committed: int
    completed: int

    @property
    def secured(self) -> int:
        return self.committed + self.completed


def commitment_counts(session: Session, request_id: int) -> CommitmentCounts:
    rows = session.exec(
        select(DonationCommitment.status).where(
            DonationCommitment.request_id == request_id
        )
    ).all()
    return CommitmentCounts(
        committed=sum(1 for value in rows if value == CommitmentStatus.COMMITTED.value),
        completed=sum(1 for value in rows if value == CommitmentStatus.COMPLETED.value),
    )


def recalculate_request_status(session: Session, blood_request: BloodRequest) -> None:
    """Derive active request state from commitment truth."""
    if blood_request.status in (
        RequestStatus.CANCELLED.value,
        RequestStatus.EXPIRED.value,
    ):
        return
    if (
        blood_request.status == RequestStatus.COMPLETED.value
        and blood_request.legacy_completion_incomplete
    ):
        return

    counts = commitment_counts(session, blood_request.id)
    if counts.completed >= blood_request.units:
        blood_request.status = RequestStatus.COMPLETED.value
    elif counts.secured >= blood_request.units:
        blood_request.status = RequestStatus.FULLY_COMMITTED.value
    elif counts.secured > 0:
        blood_request.status = RequestStatus.PARTIALLY_COMMITTED.value
    else:
        blood_request.status = RequestStatus.PENDING.value
    blood_request.updated_at = utc_now()
    session.add(blood_request)


def _locked_request(session: Session, request_id: int) -> BloodRequest:
    stmt = select(BloodRequest).where(BloodRequest.id == request_id)
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    blood_request = session.exec(stmt).first()
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return blood_request


def _commitment_for_donor(
    session: Session, request_id: int, donor_id: int
) -> DonationCommitment | None:
    return session.exec(
        select(DonationCommitment).where(
            DonationCommitment.request_id == request_id,
            DonationCommitment.donor_id == donor_id,
        )
    ).first()


def _validate_donor(blood_request: BloodRequest, user: User) -> None:
    if user.id is None:
        raise HTTPException(status_code=401, detail="Authenticated user has no id")
    if blood_request.recipient_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot accept your own request",
        )
    if not user.blood_group or not user.phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Complete your donor profile before accepting a request",
        )
    if not user.is_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are not currently available to donate",
        )
    if not is_eligible(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are not currently eligible to donate based on your recorded donation history",
        )
    if not is_blood_compatible(user.blood_group, blood_request.blood_group):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your blood group is not compatible with this request",
        )


def _next_available_slot(session: Session, blood_request: BloodRequest) -> int | None:
    used = set(
        session.exec(
            select(DonationCommitment.slot_number).where(
                DonationCommitment.request_id == blood_request.id,
                DonationCommitment.status.in_(_ACTIVE_COMMITMENT_STATUSES),
                DonationCommitment.slot_number.is_not(None),
            )
        ).all()
    )
    for slot in range(1, blood_request.units + 1):
        if slot not in used:
            return slot
    return None


def commit_to_request(session: Session, request_id: int, user: User) -> BloodRequest:
    """Secure exactly one unit slot for the authenticated donor."""
    notification = None
    try:
        blood_request = _locked_request(session, request_id)
        existing = _commitment_for_donor(session, request_id, user.id)

        # Retrying the same already-secured action is harmless and never creates
        # another commitment row.
        if existing and existing.status in _ACTIVE_COMMITMENT_STATUSES:
            return blood_request

        counts = commitment_counts(session, request_id)
        if (
            blood_request.needed_date < business_today()
            and counts.secured == 0
            and blood_request.status not in _TERMINAL_REQUEST_STATUSES
        ):
            blood_request.status = RequestStatus.EXPIRED.value
            blood_request.updated_at = utc_now()
            session.add(blood_request)
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot accept an expired request",
            )

        if blood_request.status in _TERMINAL_REQUEST_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot accept a request with status '{blood_request.status}'",
            )

        _validate_donor(blood_request, user)
        slot = _next_available_slot(session, blood_request)
        if slot is None or counts.secured >= blood_request.units:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="All requested blood units are already secured",
            )

        now = utc_now()
        if existing is None:
            commitment = DonationCommitment(
                request_id=blood_request.id,
                donor_id=user.id,
                slot_number=slot,
                status=CommitmentStatus.COMMITTED.value,
                committed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(commitment)
        else:
            commitment = existing
            commitment.slot_number = slot
            commitment.status = CommitmentStatus.COMMITTED.value
            commitment.committed_at = now
            commitment.completed_at = None
            commitment.withdrawn_at = None
            commitment.cancelled_at = None
            commitment.updated_at = now
            session.add(commitment)

        try:
            session.flush()
        except (IntegrityError, OperationalError) as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A donor secured the remaining request slot first; retry the request",
            ) from exc

        recalculate_request_status(session, blood_request)
        audit_log(
            session,
            user.id,
            "blood_request_committed",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        notification = create_notification(
            session,
            blood_request.recipient_id,
            NotificationType.ACCEPTED_REQUEST,
            "Donation Commitment Received",
            f"{user.name} committed one unit to your blood request. Phone: {user.phone or 'N/A'}.",
            data={
                "request_id": blood_request.id,
                "commitment_id": commitment.id,
                "donor_id": user.id,
            },
            send_push=False,
            commit=False,
        )
        session.commit()
        session.refresh(blood_request)
    except HTTPException:
        # Keep deliberate expiry committed; all other HTTP failures have no
        # state that needs committing.
        if session.in_transaction():
            session.rollback()
        raise
    except Exception:
        session.rollback()
        raise

    if notification is not None:
        send_notification_push(session, notification)
    return blood_request


def withdraw_commitment(session: Session, request_id: int, user: User) -> BloodRequest:
    """Release the donor's uncompleted slot."""
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    commitment = _commitment_for_donor(session, request_id, user.id)
    if commitment is None:
        raise HTTPException(status_code=404, detail="Commitment not found")
    if commitment.status == CommitmentStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A completed donation cannot be withdrawn",
        )
    if commitment.status != CommitmentStatus.COMMITTED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot withdraw a commitment with status '{commitment.status}'",
        )

    try:
        now = utc_now()
        commitment.status = CommitmentStatus.WITHDRAWN.value
        commitment.slot_number = None
        commitment.withdrawn_at = now
        commitment.updated_at = now
        session.add(commitment)
        session.flush()
        recalculate_request_status(session, blood_request)
        audit_log(
            session,
            user.id,
            "blood_request_commitment_withdrawn",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        session.commit()
        session.refresh(blood_request)
    except Exception:
        session.rollback()
        raise
    return blood_request


def confirm_commitment(
    session: Session,
    request_id: int,
    commitment_id: int,
    user: User,
) -> BloodRequest:
    """Recipient confirms one actual donation; confirmation is idempotent."""
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if blood_request.recipient_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the request recipient can confirm a donation",
        )

    commitment = session.get(DonationCommitment, commitment_id)
    if commitment is None or commitment.request_id != request_id:
        raise HTTPException(status_code=404, detail="Commitment not found")
    if commitment.status == CommitmentStatus.COMPLETED.value:
        return blood_request
    if commitment.status != CommitmentStatus.COMMITTED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot confirm a commitment with status '{commitment.status}'",
        )

    notification = None
    try:
        now = utc_now()
        donation_date = business_today()
        commitment.status = CommitmentStatus.COMPLETED.value
        commitment.completed_at = now
        commitment.updated_at = now
        session.add(commitment)

        history = session.exec(
            select(DonationHistory).where(
                DonationHistory.commitment_id == commitment.id
            )
        ).first()
        if history is None:
            session.add(
                DonationHistory(
                    donor_id=commitment.donor_id,
                    request_id=blood_request.id,
                    commitment_id=commitment.id,
                    date=donation_date,
                    recipient=blood_request.patient_name,
                    hospital=blood_request.hospital_name,
                    blood_group=blood_request.blood_group,
                    status="Completed",
                )
            )

        donor = session.get(User, commitment.donor_id)
        if donor is not None:
            donor.last_donation_date = donation_date
            session.add(donor)

        session.flush()
        recalculate_request_status(session, blood_request)
        audit_log(
            session,
            user.id,
            "blood_request_commitment_completed",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        if donor is not None:
            notification = create_notification(
                session,
                donor.id,
                NotificationType.REQUEST_COMPLETED,
                "Donation Confirmed!",
                f"Your donation to {blood_request.patient_name} at {blood_request.hospital_name} has been confirmed. Thank you!",
                data={
                    "request_id": blood_request.id,
                    "commitment_id": commitment.id,
                },
                send_push=False,
                commit=False,
            )
        session.commit()
        session.refresh(blood_request)
    except Exception:
        session.rollback()
        raise

    if notification is not None:
        send_notification_push(session, notification)
    return blood_request
