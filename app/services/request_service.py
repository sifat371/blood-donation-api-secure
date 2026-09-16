"""Blood-request lifecycle service.

REST and MCP entry points share these rules so authorisation, expiry,
transitions, notifications and audit logging cannot drift apart.
"""

from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlmodel import Session, select

from app.core.time import business_today, utc_now
from app.db.models import (
    BloodRequest,
    DonationHistory,
    NotificationType,
    RequestStatus,
    User,
)
from app.schemas.blood_request import BloodRequestResponse
from app.services.auth_service import audit_log
from app.services.eligibility import is_blood_compatible, is_eligible
from app.services.geo import haversine_distance
from app.services.notifications import create_notification, send_notification_push

NOTIFY_RADIUS_KM = 20
NOTIFY_MAX_DONORS = 10

_PUBLICLY_VISIBLE_STATUSES = (
    RequestStatus.PENDING.value,
    RequestStatus.ACCEPTED.value,
)


# ── Expiry ────────────────────────────────────────────────


def expire_stale_requests(session: Session, *, commit: bool = True) -> int:
    """Mark every overdue Pending request as Expired.

    Only Pending requests expire automatically. Once a donor has accepted a
    request, the recipient must explicitly complete or cancel it.
    """
    result = session.execute(
        update(BloodRequest)
        .where(
            BloodRequest.status == RequestStatus.PENDING.value,
            BloodRequest.needed_date < business_today(),
        )
        .values(status=RequestStatus.EXPIRED.value, updated_at=utc_now())
    )
    changed = int(result.rowcount or 0)
    if changed and commit:
        session.commit()
    return changed


def expire_request_if_stale(
    session: Session, blood_request: BloodRequest, *, commit: bool = True
) -> bool:
    if (
        blood_request.status == RequestStatus.PENDING.value
        and blood_request.needed_date < business_today()
    ):
        blood_request.status = RequestStatus.EXPIRED.value
        blood_request.updated_at = utc_now()
        session.add(blood_request)
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
    is_accepted_donor = (
        blood_request.accepted_by is not None
        and blood_request.accepted_by == user.id
    )
    if (
        not is_recipient
        and not is_accepted_donor
        and blood_request.status not in _PUBLICLY_VISIBLE_STATUSES
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

    viewer_id = viewer.id if viewer else None
    is_recipient = viewer_id == blood_request.recipient_id
    is_accepted_donor = (
        viewer_id is not None
        and blood_request.accepted_by is not None
        and viewer_id == blood_request.accepted_by
    )
    may_see_private = is_recipient or is_accepted_donor

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
        resp.accepted_by = None

    if blood_request.accepted_by and may_see_private:
        donor = session.get(User, blood_request.accepted_by)
        if donor:
            resp.donor_name = donor.name
            resp.donor_phone = donor.phone

    if distance_km is None and viewer is not None:
        distance_km = _distance_between(viewer, blood_request)
    if distance_km is not None:
        resp.distance_km = round(distance_km, 2)

    return resp


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


# ── Transitions ──────────────────────────────────────────


def _claim_pending_request(
    session: Session,
    request_id: int,
    donor_id: int,
    updated_at,
) -> bool:
    """Atomically claim a still-pending, still-unclaimed request."""
    result = session.execute(
        update(BloodRequest)
        .where(
            BloodRequest.id == request_id,
            BloodRequest.status == RequestStatus.PENDING.value,
            BloodRequest.accepted_by.is_(None),
        )
        .values(
            accepted_by=donor_id,
            status=RequestStatus.ACCEPTED.value,
            updated_at=updated_at,
        )
    )
    return int(result.rowcount or 0) == 1


def accept_request(session: Session, request_id: int, user: User) -> BloodRequest:
    """A donor atomically claims a pending request."""
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

    if expire_request_if_stale(session, blood_request):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot accept an expired request",
        )

    if blood_request.recipient_id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot accept your own request",
        )
    if blood_request.status != RequestStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot accept a request with status '{blood_request.status}'",
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

    distance = _distance_between(user, blood_request)
    distance_km = round(distance, 2) if distance is not None else None
    notification = None

    try:
        if not _claim_pending_request(session, request_id, user.id, utc_now()):
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Request was already accepted or is no longer pending",
            )

        session.expire(blood_request)
        session.refresh(blood_request)

        audit_log(
            session,
            user.id,
            "blood_request_accepted",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        notification = create_notification(
            session,
            blood_request.recipient_id,
            NotificationType.ACCEPTED_REQUEST,
            "Request Accepted!",
            f"{user.name} has accepted your blood request. "
            f"Phone: {user.phone or 'N/A'}. "
            f"{'Distance: ' + str(distance_km) + ' km' if distance_km else ''}",
            data={
                "request_id": blood_request.id,
                "donor_id": user.id,
                "donor_name": user.name,
                "donor_phone": user.phone,
                "distance_km": distance_km,
            },
            send_push=False,
            commit=False,
        )
        session.commit()
        session.refresh(blood_request)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise

    if notification is not None:
        send_notification_push(session, notification)
    return blood_request


def complete_request(session: Session, request_id: int, user: User) -> BloodRequest:
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if blood_request.recipient_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the recipient can mark a request as completed",
        )
    if blood_request.status != RequestStatus.ACCEPTED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot complete a request with status '{blood_request.status}'",
        )

    blood_request.status = RequestStatus.COMPLETED.value
    blood_request.updated_at = utc_now()
    session.add(blood_request)

    donor = None
    notification = None
    donation_date = business_today()
    if blood_request.accepted_by:
        session.add(
            DonationHistory(
                donor_id=blood_request.accepted_by,
                request_id=blood_request.id,
                date=donation_date,
                recipient=blood_request.patient_name,
                hospital=blood_request.hospital_name,
                blood_group=blood_request.blood_group,
                status="Completed",
            )
        )
        donor = session.get(User, blood_request.accepted_by)
        if donor:
            donor.last_donation_date = donation_date
            session.add(donor)

    try:
        audit_log(
            session,
            user.id,
            "blood_request_completed",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        if donor:
            notification = create_notification(
                session,
                donor.id,
                NotificationType.REQUEST_COMPLETED,
                "Donation Confirmed!",
                f"Your donation to {blood_request.patient_name} at "
                f"{blood_request.hospital_name} has been confirmed. Thank you!",
                data={"request_id": blood_request.id},
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

    previously_accepted_by = blood_request.accepted_by
    blood_request.status = RequestStatus.CANCELLED.value
    blood_request.updated_at = utc_now()
    session.add(blood_request)

    notification = None
    try:
        audit_log(
            session,
            user.id,
            "blood_request_cancelled",
            "blood_request",
            str(blood_request.id),
            commit=False,
        )
        if previously_accepted_by:
            notification = create_notification(
                session,
                previously_accepted_by,
                NotificationType.CANCELLED_REQUEST,
                "Request Cancelled",
                f"The blood request for {blood_request.patient_name} at "
                f"{blood_request.hospital_name} has been cancelled.",
                data={"request_id": blood_request.id},
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


# ── Donor fan-out on creation ────────────────────────────


def notify_nearby_donors(session: Session, blood_request: BloodRequest) -> None:
    if expire_request_if_stale(session, blood_request):
        return
    if blood_request.status != RequestStatus.PENDING.value:
        return
    if blood_request.latitude is None or blood_request.longitude is None:
        return

    stmt = select(User).where(
        User.blood_group == blood_request.blood_group,
        User.is_available == True,  # noqa: E712
        User.latitude.isnot(None),
        User.longitude.isnot(None),
        User.id != blood_request.recipient_id,
    )
    candidates = session.exec(stmt).all()

    candidates_with_dist = []
    for donor in candidates:
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
