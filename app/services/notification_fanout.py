"""Materialize durable blood-request fan-out into inbox and delivery rows."""

import json

from sqlmodel import Session, select

from app.db.models import (
    BloodRequest,
    DonationCommitment,
    Notification,
    NotificationType,
    OutboxEvent,
    RequestStatus,
    User,
)
from app.services.eligibility import is_blood_compatible, is_eligible
from app.services.geo import haversine_distance
from app.services.notifications import create_notification

NOTIFY_RADIUS_KM = 20
NOTIFY_MAX_DONORS = 10
_OPEN_REQUEST_STATUSES = (
    RequestStatus.PENDING.value,
    RequestStatus.PARTIALLY_COMMITTED.value,
)


def materialize_blood_request_created(session: Session, event: OutboxEvent) -> int:
    """Create deterministic donor notifications for one request-created event."""
    try:
        payload = json.loads(event.payload_json)
        request_id = int(payload["request_id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed blood_request_created outbox payload") from exc

    blood_request = session.get(BloodRequest, request_id)
    if blood_request is None or blood_request.status not in _OPEN_REQUEST_STATUSES:
        return 0
    if blood_request.latitude is None or blood_request.longitude is None:
        return 0

    existing_donor_ids = set(
        session.exec(
            select(DonationCommitment.donor_id).where(
                DonationCommitment.request_id == blood_request.id
            )
        ).all()
    )
    candidates = session.exec(
        select(User).where(
            User.is_available == True,  # noqa: E712
            User.latitude.isnot(None),
            User.longitude.isnot(None),
            User.id != blood_request.recipient_id,
        )
    ).all()

    nearby: list[tuple[User, float]] = []
    for donor in candidates:
        if donor.id in existing_donor_ids:
            continue
        if not donor.blood_group or not is_blood_compatible(
            donor.blood_group, blood_request.blood_group
        ):
            continue
        if not is_eligible(donor):
            continue
        distance = haversine_distance(
            blood_request.latitude,
            blood_request.longitude,
            donor.latitude,
            donor.longitude,
        )
        if distance <= NOTIFY_RADIUS_KM:
            nearby.append((donor, distance))

    nearby.sort(key=lambda item: item[1])
    created = 0
    for donor, distance in nearby[:NOTIFY_MAX_DONORS]:
        dedupe_key = f"blood_request_created:{blood_request.id}:donor:{donor.id}"
        existing = session.exec(
            select(Notification).where(Notification.dedupe_key == dedupe_key)
        ).first()
        if existing is not None:
            continue
        create_notification(
            session,
            donor.id,
            NotificationType.NEW_BLOOD_REQUEST,
            f"Urgent: {blood_request.blood_group} blood needed",
            f"{blood_request.units} unit(s) of {blood_request.blood_group} blood needed "
            f"at {blood_request.hospital_name}. Distance: {round(distance, 1)} km.",
            data={
                "request_id": blood_request.id,
                "distance_km": round(distance, 2),
            },
            dedupe_key=dedupe_key,
            commit=False,
        )
        created += 1

    return created
