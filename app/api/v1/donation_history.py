"""
Donation history endpoints

GET /donation-history — paginated history for current user
"""

from fastapi import APIRouter, Query
from sqlmodel import select

from app.core.deps import CurrentUser, DbSession
from app.db.models import DonationHistory
from app.schemas.donation_history import DonationHistoryResponse
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/donation-history", tags=["Donation History"])


@router.get("", response_model=PaginatedResponse[DonationHistoryResponse])
def list_donation_history(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    stmt = (
        select(DonationHistory)
        .where(DonationHistory.donor_id == user.id)
        .order_by(DonationHistory.date.desc())
    )
    count_stmt = select(DonationHistory).where(DonationHistory.donor_id == user.id)
    total = len(session.exec(count_stmt).all())

    items = session.exec(stmt.offset(offset).limit(limit)).all()

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )
