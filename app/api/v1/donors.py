"""
Donor search endpoints (§2.4)

GET /donors/search — find eligible donors by blood_group, location, radius
"""

from fastapi import APIRouter, Query
from sqlmodel import select

from app.core.deps import CurrentUser, DbSession
from app.db.models import User
from app.schemas.donor import DonorResponse
from app.schemas.common import PaginatedResponse
from app.services.geo import haversine_distance
from app.services.eligibility import is_eligible

router = APIRouter(prefix="/donors", tags=["Donor Search"])


@router.get("/search", response_model=PaginatedResponse[DonorResponse])
def search_donors(
    user: CurrentUser,
    session: DbSession,
    blood_group: str = Query(..., description="Blood group to search for, e.g. O+"),
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(20, ge=1, le=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Search for eligible donors matching blood_group within radius_km.

    Eligibility = is_available AND (last_donation_date is null OR >= 90 days ago).
    Results sorted by distance (ascending).
    """
    # Fetch all potential donors with matching blood group
    stmt = select(User).where(
        User.blood_group == blood_group,
        User.is_available == True,
        User.latitude.isnot(None),
        User.longitude.isnot(None),
        User.id != user.id,  # exclude self
    )
    candidates = session.exec(stmt).all()

    # Filter by eligibility + distance, compute distance
    results = []
    for donor in candidates:
        if not is_eligible(donor):
            continue
        dist = haversine_distance(latitude, longitude, donor.latitude, donor.longitude)
        if dist <= radius_km:
            results.append((donor, dist))

    # Sort by distance
    results.sort(key=lambda x: x[1])

    total = len(results)
    page = results[offset : offset + limit]

    items = [
        DonorResponse(
            id=donor.id,
            name=donor.name,
            distance_km=round(dist, 2),
            blood_group=donor.blood_group,
            last_donation_date=donor.last_donation_date,
            is_available=donor.is_available,
            division=donor.division,
            district=donor.district,
            upazila=donor.upazila,
        )
        for donor, dist in page
    ]

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )
