"""
Donor search endpoints (§2.4)

GET /donors/search — find eligible red-cell-compatible donors by requested
recipient blood group, location, and radius.
"""

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession
from app.schemas.donor import DonorResponse
from app.schemas.common import PaginatedResponse
from app.services.discovery_service import find_compatible_donors

router = APIRouter(prefix="/donors", tags=["Donor Search"])


@router.get("/search", response_model=PaginatedResponse[DonorResponse])
def search_donors(
    user: CurrentUser,
    session: DbSession,
    blood_group: str = Query(
        ..., description="Recipient/requested blood group, e.g. A+"
    ),
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(20, ge=1, le=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    results = find_compatible_donors(
        session,
        viewer=user,
        recipient_blood_group=blood_group,
        latitude=latitude,
        longitude=longitude,
        radius_km=radius_km,
    )
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
