"""Frontend-facing donor commitment queries and recipient release action."""

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.time import utc_now
from app.db.models import BloodRequest, CommitmentStatus, DonationCommitment, User
from app.schemas.blood_request import CommitmentResponse, DonorCommitmentResponse
from app.services.auth_service import audit_log
from app.services.commitment_service import recalculate_request_status
from app.services.notifications import create_notification
from app.services import request_service

COMMITMENT_RELEASED_NOTIFICATION = "Commitment Released"


def _locked_request(session: Session, request_id: int) -> BloodRequest:
    stmt = select(BloodRequest).where(BloodRequest.id == request_id)
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    blood_request = session.exec(stmt).first()
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return blood_request


def _commitment_response(session: Session, commitment: DonationCommitment) -> CommitmentResponse:
    item = CommitmentResponse.model_validate(commitment)
    item.donor_id = commitment.donor_id
    donor = session.get(User, commitment.donor_id)
    if donor is not None:
        item.donor_name = donor.name
        item.donor_phone = donor.phone
    return item


def list_active_donor_commitments(
    session: Session,
    donor: User,
    *,
    limit: int,
    offset: int,
) -> tuple[list[DonorCommitmentResponse], int]:
    """Return the donor's active commitments and authorized request details."""
    base = select(DonationCommitment).where(
        DonationCommitment.donor_id == donor.id,
        DonationCommitment.status == CommitmentStatus.COMMITTED.value,
    )
    all_rows = list(session.exec(base).all())
    total = len(all_rows)
    rows = list(
        session.exec(
            base.order_by(DonationCommitment.committed_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )

    items: list[DonorCommitmentResponse] = []
    for commitment in rows:
        blood_request = session.get(BloodRequest, commitment.request_id)
        if blood_request is None:
            continue
        items.append(
            DonorCommitmentResponse(
                commitment=_commitment_response(session, commitment),
                request=request_service.build_response(
                    session, blood_request, viewer=donor
                ),
            )
        )
    return items, total


def release_commitment(
    session: Session,
    request_id: int,
    commitment_id: int,
    recipient: User,
) -> BloodRequest:
    """Release one uncompleted donor commitment and reopen its request slot."""
    try:
        blood_request = _locked_request(session, request_id)
        if blood_request.recipient_id != recipient.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the recipient can release a donor commitment",
            )

        commitment = session.get(DonationCommitment, commitment_id)
        if commitment is None or commitment.request_id != request_id:
            raise HTTPException(status_code=404, detail="Commitment not found")

        if commitment.status == CommitmentStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A completed donation cannot be released",
            )
        if commitment.status == CommitmentStatus.CANCELLED.value:
            return blood_request
        if commitment.status != CommitmentStatus.COMMITTED.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot release a commitment with status '{commitment.status}'",
            )

        now = utc_now()
        commitment.status = CommitmentStatus.CANCELLED.value
        commitment.slot_number = None
        commitment.cancelled_at = now
        commitment.updated_at = now
        session.add(commitment)
        recalculate_request_status(session, blood_request)

        audit_log(
            session,
            recipient.id,
            "donation_commitment_released",
            "donation_commitment",
            str(commitment.id),
            commit=False,
        )
        create_notification(
            session,
            commitment.donor_id,
            COMMITMENT_RELEASED_NOTIFICATION,
            "Donation Commitment Released",
            f"Your commitment for the blood request at {blood_request.hospital_name} was released by the recipient.",
            data={
                "request_id": blood_request.id,
                "commitment_id": commitment.id,
            },
            commit=False,
            send_push=False,
        )
        session.commit()
        session.refresh(blood_request)
        return blood_request
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
