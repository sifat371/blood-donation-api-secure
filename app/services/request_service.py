"""
Blood-request lifecycle service.

Single home for the accept / complete / cancel rules and for the
"who is allowed to see this request" rule. Both the REST layer
(`app/api/v1/blood_requests.py`) and the AI agent's MCP tools
(`app/mcp_tools/tools.py`) call into here, so authorisation, status
transitions, notifications and audit logging can't drift apart between the
two entry points.

Functions raise `fastapi.HTTPException` so the REST layer can return them
directly; the MCP dispatcher catches them and converts to `{"error": ...}`.
"""

from datetime import datetime
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.db.models import (
    BloodRequest,
    DonationHistory,
    NotificationType,
    RequestStatus,
    User,
)
from app.schemas.blood_request import BloodRequestResponse
from app.services.auth_service import audit_log
from app.services.eligibility import is_eligible, is_blood_compatible
from app.services.geo import haversine_distance
from app.services.notifications import create_notification

# Radius and fan-out cap for the "new request" donor notification.
NOTIFY_RADIUS_KM = 20
NOTIFY_MAX_DONORS = 10

# Statuses a request must be in to be visible to someone who is neither the
# recipient nor the accepted donor. Pending is the public appeal for blood;
# Accepted is included so a donor who taps a slightly stale notification sees
# "already accepted" instead of a misleading 404.
_PUBLICLY_VISIBLE_STATUSES = (
    RequestStatus.PENDING.value,
    RequestStatus.ACCEPTED.value,
)


# ── Lookup + visibility ──────────────────────────────────


def get_request_for_user(
    session: Session, request_id: int, user: User
) -> BloodRequest:
    """
    Fetch a request the given user is allowed to see, or raise 404.

    Deliberately returns 404 (not 403) when a request exists but isn't visible
    to this user, so the endpoint can't be used to enumerate which request IDs
    exist.
    """
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

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
    """
    Serialise a request, enriching it with names and redacting the donor's
    contact details from anyone who isn't the recipient or the donor.
    """
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
        # Public discovery exposes only what a prospective donor needs to decide
        # whether to respond. Direct contact, patient identity, exact coordinates
        # and account identifiers are released only after a donor is accepted.
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


def accept_request(
    session: Session, request_id: int, user: User
) -> BloodRequest:
    """A donor claims a pending request. Notifies the recipient."""
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

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

    blood_request.accepted_by = user.id
    blood_request.status = RequestStatus.ACCEPTED.value
    blood_request.updated_at = datetime.utcnow()
    session.add(blood_request)
    session.commit()
    session.refresh(blood_request)

    audit_log(
        session,
        user.id,
        "blood_request_accepted",
        "blood_request",
        str(blood_request.id),
    )

    distance = _distance_between(user, blood_request)
    distance_km = round(distance, 2) if distance is not None else None

    create_notification(
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
    )

    return blood_request


def complete_request(
    session: Session, request_id: int, user: User
) -> BloodRequest:
    """
    The recipient confirms the donation happened. Records donation history,
    refreshes the donor's last_donation_date (which drives the 90-day
    eligibility rule) and notifies the donor.
    """
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
    blood_request.updated_at = datetime.utcnow()
    session.add(blood_request)

    donor = None
    if blood_request.accepted_by:
        session.add(
            DonationHistory(
                donor_id=blood_request.accepted_by,
                request_id=blood_request.id,
                date=datetime.utcnow().date(),
                recipient=blood_request.patient_name,
                hospital=blood_request.hospital_name,
                blood_group=blood_request.blood_group,
                status="Completed",
            )
        )
        donor = session.get(User, blood_request.accepted_by)
        if donor:
            donor.last_donation_date = datetime.utcnow().date()
            session.add(donor)

    session.commit()
    session.refresh(blood_request)

    audit_log(
        session,
        user.id,
        "blood_request_completed",
        "blood_request",
        str(blood_request.id),
    )

    if donor:
        create_notification(
            session,
            donor.id,
            NotificationType.REQUEST_COMPLETED,
            "Donation Confirmed!",
            f"Your donation to {blood_request.patient_name} at "
            f"{blood_request.hospital_name} has been confirmed. Thank you!",
            data={"request_id": blood_request.id},
        )

    return blood_request


def cancel_request(
    session: Session, request_id: int, user: User
) -> BloodRequest:
    """The recipient withdraws the request. Notifies the donor if one accepted."""
    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

    if blood_request.recipient_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the recipient can cancel a request",
        )

    if blood_request.status in (
        RequestStatus.COMPLETED.value,
        RequestStatus.CANCELLED.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel a request with status '{blood_request.status}'",
        )

    previously_accepted_by = blood_request.accepted_by
    blood_request.status = RequestStatus.CANCELLED.value
    blood_request.updated_at = datetime.utcnow()
    session.add(blood_request)
    session.commit()
    session.refresh(blood_request)

    audit_log(
        session,
        user.id,
        "blood_request_cancelled",
        "blood_request",
        str(blood_request.id),
    )

    if previously_accepted_by:
        create_notification(
            session,
            previously_accepted_by,
            NotificationType.CANCELLED_REQUEST,
            "Request Cancelled",
            f"The blood request for {blood_request.patient_name} at "
            f"{blood_request.hospital_name} has been cancelled.",
            data={"request_id": blood_request.id},
        )

    return blood_request


# ── Donor fan-out on creation ────────────────────────────


def notify_nearby_donors(session: Session, blood_request: BloodRequest) -> None:
    """
    Notify the nearest eligible, available donors whose blood group matches.

    Capped at NOTIFY_MAX_DONORS within NOTIFY_RADIUS_KM so a single request
    can't blast the whole user base.
    """
    if not blood_request.latitude or not blood_request.longitude:
        return

    stmt = select(User).where(
        User.blood_group == blood_request.blood_group,
        User.is_available == True,  # noqa: E712 — SQLModel needs the comparison
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

    candidates_with_dist.sort(key=lambda x: x[1])

    for donor, dist in candidates_with_dist[:NOTIFY_MAX_DONORS]:
        create_notification(
            session,
            donor.id,
            NotificationType.NEW_BLOOD_REQUEST,
            f"Urgent: {blood_request.blood_group} blood needed",
            f"{blood_request.units} unit(s) of {blood_request.blood_group} blood needed "
            f"at {blood_request.hospital_name}. "
            f"Distance: {round(dist, 1)} km.",
            data={
                "request_id": blood_request.id,
                "distance_km": round(dist, 2),
            },
        )


def notify_nearby_donors_task(request_id: int) -> None:
    """
    Background-task entry point: runs after the HTTP response has been sent,
    on its own DB session.

    The engine is imported inside the function so tests can point
    `app.db.database.engine` at their in-memory database.
    """
    from app.db.database import engine

    with Session(engine) as session:
        blood_request = session.get(BloodRequest, request_id)
        if blood_request is None:
            return
        notify_nearby_donors(session, blood_request)
