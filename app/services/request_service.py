"""Blood-request lifecycle and privacy service.

P2 keeps BloodRequest as the parent aggregate and stores donor participation in
DonationCommitment rows. REST and MCP entry points use this module so expiry,
visibility, cancellation, compatibility routes and donor fan-out stay aligned.
"""

from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.time import business_today, utc_now
from app.db.models import (
    BloodRequest,
    CommitmentStatus,
    DonationCommitment,
    NotificationType,
    RequestStatus,
    User,
)
from app.schemas.blood_request import BloodRequestResponse, CommitmentResponse
from app.services.auth_service import audit_log
from app.services.commitment_service import (
    commitment_counts,
    commit_to_request,
    confirm_commitment,
    recalculate_request_status,
    withdraw_commitment,
)
from app.services.eligibility import is_blood_compatible, is_eligible
from app.services.geo import haversine_distance
from app.services.notifications import create_notification, send_notification_push

NOTIFY_RADIUS_KM = 20
NOTIFY_MAX_DONORS = 10

_OPEN_REQUEST_STATUSES = (
    RequestStatus.PENDING.value,
    RequestStatus.PARTIALLY_COMMITTED.value,
)
_SECURED_COMMITMENT_STATUSES = (
    CommitmentStatus.COMMITTED.value,
    CommitmentStatus.COMPLETED.value,
)


# ── Commitment helpers ───────────────────────────────────


def _commitments_for_request(session: Session, request_id: int) -> list[DonationCommitment]:
    return list(
        session.exec(
            select(DonationCommitment)
            .where(DonationCommitment.request_id == request_id)
            .order_by(DonationCommitment.id)
        ).all()
    )


def _viewer_commitment(
    session: Session, request_id: int, user_id: Optional[int]
) -> Optional[DonationCommitment]:
    if user_id is None:
        return None
    return session.exec(
        select(DonationCommitment).where(
            DonationCommitment.request_id == request_id,
            DonationCommitment.donor_id == user_id,
        )
    ).first()


def has_completed_commitment(session: Session, request_id: int, user_id: int) -> bool:
    return (
        session.exec(
            select(DonationCommitment.id).where(
                DonationCommitment.request_id == request_id,
                DonationCommitment.donor_id == user_id,
                DonationCommitment.status == CommitmentStatus.COMPLETED.value,
            )
        ).first()
        is not None
    )


# ── Expiry ───────────────────────────────────────────────


def _expire_request_in_transaction(
    session: Session, blood_request: BloodRequest
) -> list[DonationCommitment]:
    now = utc_now()
    blood_request.status = RequestStatus.EXPIRED.value
    blood_request.updated_at = now
    session.add(blood_request)

    cancelled: list[DonationCommitment] = []
    for commitment in _commitments_for_request(session, blood_request.id):
        if commitment.status != CommitmentStatus.COMMITTED.value:
            continue
        commitment.status = CommitmentStatus.CANCELLED.value
        commitment.slot_number = None
        commitment.cancelled_at = now
        commitment.updated_at = now
        session.add(commitment)
        cancelled.append(commitment)
    return cancelled


def expire_stale_requests(session: Session, *, commit: bool = True) -> int:
    stale = session.exec(
        select(BloodRequest).where(
            BloodRequest.status.in_(_OPEN_REQUEST_STATUSES),
            BloodRequest.needed_date < business_today(),
        )
    ).all()
    for blood_request in stale:
        _expire_request_in_transaction(session, blood_request)
    if stale:
        if commit:
            session.commit()
        else:
            session.flush()
    return len(stale)


def expire_request_if_stale(
    session: Session, blood_request: BloodRequest, *, commit: bool = True
) -> bool:
    if (
        blood_request.status in _OPEN_REQUEST_STATUSES
        and blood_request.needed_date < business_today()
    ):
        _expire_request_in_transaction(session, blood_request)
        if commit:
            session.commit()
            session.refresh(blood_request)
        else:
            session.flush()
        return True
    return False


# ── Lookup + visibility ──────────────────────────────────


def get_request_for_user(
    session: Session, request_id: int, user: User
) -> BloodRequest:
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

    expire_request_if_stale(session, blood_request)

    is_recipient = blood_request.recipient_id == user.id
    viewer_commitment = _viewer_commitment(session, request_id, user.id)
    if (
        not is_recipient
        and viewer_commitment is None
        and blood_request.status not in _OPEN_REQUEST_STATUSES
    ):
        raise HTTPException(status_code=404, detail="Request not found")

    return blood_request


def build_response(
    session: Session,
    blood_request: BloodRequest,
    viewer: Optional[User] = None,
    distance_km: Optional[float] = None,
) -> BloodRequestResponse:
    resp = BloodRequestResponse.model_validate(blood_request)
    counts = commitment_counts(session, blood_request.id)
    commitments = _commitments_for_request(session, blood_request.id)

    resp.units_required = blood_request.units
    resp.units_committed = counts.committed
    resp.units_completed = counts.completed
    resp.remaining_units = max(blood_request.units - counts.secured, 0)
    resp.legacy_completion_incomplete = blood_request.legacy_completion_incomplete

    viewer_id = viewer.id if viewer else None
    is_recipient = viewer_id == blood_request.recipient_id
    viewer_commitment = _viewer_commitment(session, blood_request.id, viewer_id)
    if viewer_commitment is not None:
        resp.my_commitment_status = viewer_commitment.status

    is_secured_donor = bool(
        viewer_commitment
        and viewer_commitment.status in _SECURED_COMMITMENT_STATUSES
    )
    may_see_private = is_recipient or is_secured_donor

    recipient = session.get(User, blood_request.recipient_id)
    if recipient and may_see_private:
        resp.recipient_name = recipient.name

    if not may_see_private:
        resp.recipient_id = None
        resp.patient_name = None
        resp.hospital_address = None
        resp.latitude = None
        resp.longitude = None
        resp.contact_number = None
        resp.notes = None
        resp.recipient_name = None

    # Deprecated P1 singular-donor fields remain only when exactly one secured
    # commitment is visible to this viewer. Multiple donors are never collapsed
    # into a misleading singular identity.
    visible_commitments: list[DonationCommitment] = []
    if is_recipient:
        visible_commitments = [
            item
            for item in commitments
            if item.status in _SECURED_COMMITMENT_STATUSES
        ]
    elif is_secured_donor and viewer_commitment is not None:
        visible_commitments = [viewer_commitment]

    if len(visible_commitments) == 1:
        donor_commitment = visible_commitments[0]
        donor = session.get(User, donor_commitment.donor_id)
        resp.accepted_by = donor_commitment.donor_id
        if donor is not None:
            resp.donor_name = donor.name
            resp.donor_phone = donor.phone
    else:
        resp.accepted_by = None
        resp.donor_name = None
        resp.donor_phone = None

    if distance_km is None and viewer is not None:
        distance_km = _distance_between(viewer, blood_request)
    if distance_km is not None:
        resp.distance_km = round(distance_km, 2)

    return resp


def list_commitments_for_user(
    session: Session, request_id: int, user: User
) -> list[CommitmentResponse]:
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    expire_request_if_stale(session, blood_request)

    all_commitments = _commitments_for_request(session, request_id)
    if blood_request.recipient_id == user.id:
        visible = all_commitments
    else:
        visible = [item for item in all_commitments if item.donor_id == user.id]
        if not visible:
            raise HTTPException(status_code=404, detail="Request not found")

    output: list[CommitmentResponse] = []
    for commitment in visible:
        item = CommitmentResponse.model_validate(commitment)
        item.donor_id = commitment.donor_id
        donor = session.get(User, commitment.donor_id)
        if donor is not None:
            item.donor_name = donor.name
            item.donor_phone = donor.phone
        output.append(item)
    return output


def _distance_between(user: User, blood_request: BloodRequest) -> Optional[float]:
    if None in (
        user.latitude,
        user.longitude,
        blood_request.latitude,
        blood_request.longitude,
    ):
        return None
    return haversine_distance(
        user.latitude,
        user.longitude,
        blood_request.latitude,
        blood_request.longitude,
    )


# ── Lifecycle compatibility + cancellation ──────────────


def accept_request(session: Session, request_id: int, user: User) -> BloodRequest:
    """P1-compatible route name: secure one donor commitment/unit."""
    return commit_to_request(session, request_id, user)


def complete_request(session: Session, request_id: int, user: User) -> BloodRequest:
    """Compatibility route; individual donations are confirmed by commitment."""
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if blood_request.recipient_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the recipient can mark a request as completed",
        )

    if (
        blood_request.status == RequestStatus.COMPLETED.value
        and blood_request.legacy_completion_incomplete
    ):
        return blood_request

    counts = commitment_counts(session, request_id)
    if counts.completed != blood_request.units:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirm every donated unit before completing the request",
        )

    if blood_request.status != RequestStatus.COMPLETED.value:
        try:
            recalculate_request_status(session, blood_request)
            audit_log(
                session,
                user.id,
                "blood_request_completed",
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


def cancel_request(session: Session, request_id: int, user: User) -> BloodRequest:
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

    expire_request_if_stale(session, blood_request)

    if blood_request.recipient_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the recipient can cancel a request",
        )
    if blood_request.status in (
        RequestStatus.COMPLETED.value,
        RequestStatus.CANCELLED.value,
        RequestStatus.EXPIRED.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a request with status '{blood_request.status}'",
        )

    notifications = []
    try:
        now = utc_now()
        blood_request.status = RequestStatus.CANCELLED.value
        blood_request.updated_at = now
        session.add(blood_request)

        for commitment in _commitments_for_request(session, request_id):
            if commitment.status != CommitmentStatus.COMMITTED.value:
                continue
            commitment.status = CommitmentStatus.CANCELLED.value
            commitment.slot_number = None
            commitment.cancelled_at = now
            commitment.updated_at = now
            session.add(commitment)
            notification = create_notification(
                session,
                commitment.donor_id,
                NotificationType.CANCELLED_REQUEST,
                "Request Cancelled",
                f"The blood request for {blood_request.patient_name} at "
                f"{blood_request.hospital_name} has been cancelled.",
                data={
                    "request_id": blood_request.id,
                    "commitment_id": commitment.id,
                },
                send_push=False,
                commit=False,
            )
            notifications.append(notification)

        audit_log(
            session,
            user.id,
            "blood_request_cancelled",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        session.commit()
        session.refresh(blood_request)
    except Exception:
        session.rollback()
        raise

    for notification in notifications:
        send_notification_push(session, notification)
    return blood_request


# ── Donor fan-out on creation ────────────────────────────


def notify_nearby_donors(session: Session, blood_request: BloodRequest) -> None:
    if expire_request_if_stale(session, blood_request):
        return
    if blood_request.status not in _OPEN_REQUEST_STATUSES:
        return
    if blood_request.latitude is None or blood_request.longitude is None:
        return

    stmt = select(User).where(
        User.is_available == True,  # noqa: E712
        User.latitude.isnot(None),
        User.longitude.isnot(None),
        User.id != blood_request.recipient_id,
    )
    candidates = session.exec(stmt).all()

    existing_donor_ids = {
        item.donor_id for item in _commitments_for_request(session, blood_request.id)
    }
    candidates_with_dist = []
    for donor in candidates:
        if donor.id in existing_donor_ids:
            continue
        if not donor.blood_group or not is_blood_compatible(
            donor.blood_group, blood_request.blood_group
        ):
            continue
        if not is_eligible(donor):
            continue
        dist = haversine_distance(
            blood_request.latitude,
            blood_request.longitude,
            donor.latitude,
            donor.longitude,
        )
        if dist <= NOTIFY_RADIUS_KM:
            candidates_with_dist.append((donor, dist))

    candidates_with_dist.sort(key=lambda item: item[1])
    for donor, dist in candidates_with_dist[:NOTIFY_MAX_DONORS]:
        create_notification(
            session,
            donor.id,
            NotificationType.NEW_BLOOD_REQUEST,
            f"Urgent: {blood_request.blood_group} blood needed",
            f"{blood_request.units} unit(s) of {blood_request.blood_group} blood needed "
            f"at {blood_request.hospital_name}. Distance: {round(dist, 1)} km.",
            data={
                "request_id": blood_request.id,
                "distance_km": round(dist, 2),
            },
        )


def notify_nearby_donors_task(request_id: int) -> None:
    from app.db.database import engine

    with Session(engine) as session:
        blood_request = session.get(BloodRequest, request_id)
        if blood_request is None:
            return
        notify_nearby_donors(session, blood_request)
