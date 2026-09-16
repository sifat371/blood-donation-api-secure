"""Pydantic v2 schemas — user / profile."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.db.models import BloodGroup


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    profile_photo: Optional[str] = None
    phone: Optional[str] = None
    blood_group: Optional[str] = None
    division: Optional[str] = None
    district: Optional[str] = None
    upazila: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    last_donation_date: Optional[date] = None
    is_available: bool = True
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    # Account state, so the app can show how this user signs in without having
    # to guess. Never includes google_id or the password hash.
    email_verified: bool = True
    auth_provider: str = "google"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProfileUpdate(BaseModel):
    """
    Partial profile update. Only the fields present in the request body are
    written (see `exclude_unset` in the endpoint).

    Deliberately excludes identity and account fields — email, google_id, id —
    so a user can't repoint their own account. FCM tokens go through
    POST /profile/fcm-token, which supports several devices per account;
    accepting one here would silently do nothing (it isn't a User column).
    """
    name: Optional[str] = None
    phone: Optional[str] = None
    blood_group: Optional[BloodGroup] = None
    division: Optional[str] = None
    district: Optional[str] = None
    upazila: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    is_available: Optional[bool] = None
    gender: Optional[str] = None
    date_of_birth: Optional[date] = None
    profile_photo: Optional[str] = None

    # Store the plain "O+" string rather than the enum member. Donor search
    # compares blood_group by string equality, so an unvalidated free-text group
    # ("o+", "O positive", a typo) would make the user permanently invisible to
    # every search without any error to explain why.
    model_config = {"use_enum_values": True}


class ProfileComplete(BaseModel):
    """
    Required fields for completing a profile after first sign-in.

    `google_id` is intentionally absent: it is set server-side from the
    verified Google ID token during login and must not be settable by a client.
    """
    phone: str
    blood_group: BloodGroup
    division: str
    district: str
    upazila: str
    gender: str
    date_of_birth: date
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    # See ProfileUpdate: validated against the enum, stored as a plain string.
    model_config = {"use_enum_values": True}
