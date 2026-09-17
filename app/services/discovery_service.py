"""Shared donor and blood-request discovery rules for REST and MCP."""

from typing import Optional

from sqlmodel import Session, select

from app.db.models import (
    BloodRequest,
    CommitmentStatus,
    DonationCommitment,
    RequestStatus,
    User,
)
from app.services.commitment_service import commitment_counts
from app.services.eligibility import is_blood_compatible, is_eligible
from app.services.geo import haversine_distance
from app.services import request_service

_OPEN_REQUEST_STATUSES = (
    RequestStatus.PENDING.value,
    RequestStatus.PARTIALLY_COMMITTED.value,
)
_ACTIVE_DONOR_COMMITMENT_STATUSES = (
    CommitmentStatus.COMMITTED.value,
    CommitmentStatus.COMPLETED.value,
)


def find_compatible_donors(
    session: Session,
    *,
    viewer: User,
    recipient_blood_group: str,
    latitude: float,
    longitude: float,
    radius_km: float,
    division: Optional[str] = None,
    district: Optional[str] = None,
    upazila: Optional[str] = None,
) -> list[tuple[User, float]]:
    """Return eligible red-cell-compatible donors ordered by distance."""
    stmt = select(User).where(
        User.is_available == True,  # noqa: E712
        User.latitude.isnot(None),
        User.longitude.isnot(None),
        User.blood_group.isnot(None),
        User.id != viewer.id,
    )
    if division:
        stmt = stmt.where(User.division == division)
    if district:
        stmt = stmt.where(User.district == district)
    if upazila:
        stmt = stmt.where(User.upazila == upazila)

    results: list[tuple[User, float]] = []
    for donor in session.exec(stmt).all():
        if not is_blood_compatible(donor.blood_group, recipient_blood_group):
            continue
        if not is_eligible(donor):
            continue
        distance = haversine_distance(
            latitude,
            longitude,
            donor.latitude,
            donor.longitude,
        )
        if distance <= radius_km:
            results.append((donor, distance))

    results.sort(key=lambda item: item[1])
    return results


def _donor_ready_for_request_discovery(donor: User) -> bool:
    return bool(
        donor.blood_group
        and donor.phone
        and donor.is_available
        and is_eligible(donor)
    )


def find_acceptable_nearby_requests(
    session: Session,
    *,
    donor: User,
    latitude: float,
    longitude: float,
    radius_km: float,
) -> list[tuple[BloodRequest, float]]:
    """Return only open nearby requests this donor could currently accept."""
    request_service.expire_stale_requests(session)
    if not _donor_ready_for_request_discovery(donor):
        return []

    stmt = select(BloodRequest).where(
        BloodRequest.status.in_(_OPEN_REQUEST_STATUSES),
        BloodRequest.latitude.isnot(None),
        BloodRequest.longitude.isnot(None),
        BloodRequest.recipient_id != donor.id,
    )

    results: list[tuple[BloodRequest, float]] = []
    for blood_request in session.exec(stmt).all():
        if not is_blood_compatible(donor.blood_group, blood_request.blood_group):
            continue
        if commitment_counts(session, blood_request.id).secured >= blood_request.units:
            continue
        existing = session.exec(
            select(DonationCommitment.id).where(
                DonationCommitment.request_id == blood_request.id,
                DonationCommitment.donor_id == donor.id,
                DonationCommitment.status.in_(_ACTIVE_DONOR_COMMITMENT_STATUSES),
            )
        ).first()
        if existing is not None:
            continue
        distance = haversine_distance(
            latitude,
            longitude,
            blood_request.latitude,
            blood_request.longitude,
        )
        if distance <= radius_km:
            results.append((blood_request, distance))

    results.sort(key=lambda item: item[1])
    return results
