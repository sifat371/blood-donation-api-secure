"""
Profile endpoints (§2.3)

GET    /profile/me                    — current user profile
PATCH  /profile/me                    — update profile fields
POST   /profile/complete              — complete profile after first sign-in
POST   /profile/fcm-token             — create/update one FCM installation
DELETE /profile/fcm-token/{device_id} — unregister one caller-owned installation
GET    /profile/donation-history      — paginated donation history
"""

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.deps import CurrentUser, DbSession
from app.core.time import utc_now
from app.db.models import DonationHistory
from app.schemas.user import UserResponse, ProfileUpdate, ProfileComplete
from app.schemas.donation_history import DonationHistoryResponse
from app.schemas.common import PaginatedResponse
from app.schemas.notification import FCMTokenUpsertRequest
from app.services import device_service

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/me", response_model=UserResponse)
def get_my_profile(user: CurrentUser):
    return user


@router.patch("/me", response_model=UserResponse)
def update_profile(body: ProfileUpdate, user: CurrentUser, session: DbSession):
    """
    Update the authenticated user's own profile.

    `user` comes from the Bearer token, never from the request body, so this
    can only ever modify the caller's own row.
    """
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    for key, value in update_data.items():
        setattr(user, key, value)
    if "latitude" in update_data or "longitude" in update_data:
        user.last_location_updated = utc_now()
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.post("/complete", response_model=UserResponse)
def complete_profile(body: ProfileComplete, user: CurrentUser, session: DbSession):
    """
    Fill in required profile fields after first sign-in.

    Uses exclude_none so that submitting the form without GPS permission
    doesn't wipe coordinates the user has already set.
    """
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(user, key, value)
    if body.latitude is not None and body.longitude is not None:
        user.last_location_updated = utc_now()
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.post("/fcm-token")
def register_fcm_token(
    body: FCMTokenUpsertRequest,
    user: CurrentUser,
    session: DbSession,
):
    """Create or update one authenticated user's app installation."""
    device_service.register_device(
        session,
        user.id,
        body.device_id,
        body.fcm_token,
        body.device_info,
    )
    return {"message": "FCM device registered successfully."}


@router.delete("/fcm-token/{device_id}")
def unregister_fcm_token(device_id: str, user: CurrentUser, session: DbSession):
    """Disable one caller-owned installation without exposing other devices."""
    device_service.unregister_device(session, user.id, device_id)
    return {"message": "FCM device unregistered successfully."}


@router.get("/donation-history", response_model=PaginatedResponse[DonationHistoryResponse])
def donation_history(
    user: CurrentUser,
    session: DbSession,
    limit: int = 20,
    offset: int = 0,
):
    stmt = (
        select(DonationHistory)
        .where(DonationHistory.donor_id == user.id)
        .order_by(DonationHistory.date.desc())
    )
    # Total count
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
