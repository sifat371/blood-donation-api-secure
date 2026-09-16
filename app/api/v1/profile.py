"""
Profile endpoints (§2.3)

GET   /profile/me               — current user profile
PATCH /profile/me               — update profile fields
POST  /profile/complete         — complete profile after first sign-in
POST  /profile/fcm-token        — create/update FCM token for current user
GET   /profile/donation-history — paginated donation history
"""

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.deps import CurrentUser, DbSession
from app.core.time import utc_now
from app.db.models import DonationHistory, FCMToken
from app.schemas.user import UserResponse, ProfileUpdate, ProfileComplete
from app.schemas.donation_history import DonationHistoryResponse
from app.schemas.common import PaginatedResponse
from app.schemas.notification import FCMTokenUpsertRequest

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
def register_fcm_token(body: FCMTokenUpsertRequest, user: CurrentUser, session: DbSession):
    """Create or update the current user's FCM token."""
    existing_for_user = session.exec(
        select(FCMToken).where(FCMToken.user_id == user.id)
    ).first()

    if existing_for_user:
        existing_for_user.token = body.fcm_token
        if body.device_info:
            existing_for_user.device_info = body.device_info
        existing_for_user.created_at = utc_now()
        session.add(existing_for_user)
        session.commit()
        session.refresh(existing_for_user)
        return {"message": "FCM token updated successfully."}

    existing_for_token = session.exec(
        select(FCMToken).where(FCMToken.token == body.fcm_token)
    ).first()

    if existing_for_token:
        existing_for_token.user_id = user.id
        if body.device_info:
            existing_for_token.device_info = body.device_info
        existing_for_token.created_at = utc_now()
        session.add(existing_for_token)
        session.commit()
        session.refresh(existing_for_token)
        return {"message": "FCM token updated successfully."}

    new_token = FCMToken(
        user_id=user.id,
        token=body.fcm_token,
        device_info=body.device_info or "android",
    )
    session.add(new_token)
    session.commit()
    session.refresh(new_token)

    return {"message": "FCM token registered successfully."}

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
