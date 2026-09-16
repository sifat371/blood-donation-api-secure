"""Blood Donation App — SQLModel table definitions."""

from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field, Column
from sqlalchemy import CheckConstraint, Text, UniqueConstraint

from app.core.time import utc_now


class BloodGroup(str, Enum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"


class RequestStatus(str, Enum):
    PENDING = "Pending"
    # Migration input compatibility only. P2 runtime never writes Accepted.
    ACCEPTED = "Accepted"
    PARTIALLY_COMMITTED = "Partially Committed"
    FULLY_COMMITTED = "Fully Committed"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class CommitmentStatus(str, Enum):
    COMMITTED = "Committed"
    COMPLETED = "Completed"
    WITHDRAWN = "Withdrawn"
    CANCELLED = "Cancelled"


class NotificationType(str, Enum):
    NEW_BLOOD_REQUEST = "New Blood Request"
    ACCEPTED_REQUEST = "Accepted Request"
    CANCELLED_REQUEST = "Cancelled Request"
    REQUEST_COMPLETED = "Request Completed"
    PROFILE_REMINDER = "Profile Reminder"
    DONATION_ELIGIBLE_REMINDER = "Donation Eligible Reminder"


class ConversationRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AuthProvider(str, Enum):
    GOOGLE = "google"
    PASSWORD = "password"
    BOTH = "both"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    google_id: Optional[str] = Field(default=None, unique=True, index=True)
    hashed_password: Optional[str] = Field(default=None)
    email_verified: bool = Field(default=False)
    email_verified_at: Optional[datetime] = Field(default=None)
    auth_provider: str = Field(default=AuthProvider.GOOGLE.value)
    profile_photo: Optional[str] = Field(default=None)
    phone: Optional[str] = Field(default=None)
    blood_group: Optional[str] = Field(default=None)
    division: Optional[str] = Field(default=None)
    district: Optional[str] = Field(default=None)
    upazila: Optional[str] = Field(default=None)
    latitude: Optional[float] = Field(default=None)
    longitude: Optional[float] = Field(default=None)
    last_location_updated: Optional[datetime] = Field(default=None)
    last_donation_date: Optional[date] = Field(default=None)
    is_available: bool = Field(default=True)
    gender: Optional[str] = Field(default=None)
    date_of_birth: Optional[date] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FCMToken(SQLModel, table=True):
    __tablename__ = "fcm_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token: str = Field(unique=True, index=True)
    device_info: str = Field(default="android")
    created_at: datetime = Field(default_factory=utc_now)


class BloodRequest(SQLModel, table=True):
    __tablename__ = "blood_requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    recipient_id: int = Field(foreign_key="users.id", index=True)
    patient_name: str
    blood_group: str
    units: int = Field(default=1)
    hospital_name: str
    hospital_address: Optional[str] = Field(default=None)
    latitude: Optional[float] = Field(default=None)
    longitude: Optional[float] = Field(default=None)
    needed_date: date
    contact_number: str
    notes: Optional[str] = Field(default=None, sa_column=Column(Text))
    status: str = Field(default=RequestStatus.PENDING.value)
    legacy_completion_incomplete: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DonationCommitment(SQLModel, table=True):
    __tablename__ = "donation_commitments"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "donor_id", name="uq_commitment_request_donor"
        ),
        UniqueConstraint(
            "request_id", "slot_number", name="uq_commitment_request_slot"
        ),
        CheckConstraint(
            "slot_number IS NULL OR slot_number > 0",
            name="ck_commitment_positive_slot",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="blood_requests.id", index=True)
    donor_id: int = Field(foreign_key="users.id", index=True)
    slot_number: Optional[int] = Field(default=None)
    status: str = Field(default=CommitmentStatus.COMMITTED.value, index=True)
    committed_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = Field(default=None)
    withdrawn_at: Optional[datetime] = Field(default=None)
    cancelled_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DonationHistory(SQLModel, table=True):
    __tablename__ = "donation_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    donor_id: int = Field(foreign_key="users.id", index=True)
    request_id: Optional[int] = Field(default=None, foreign_key="blood_requests.id")
    commitment_id: Optional[int] = Field(
        default=None,
        foreign_key="donation_commitments.id",
        unique=True,
        index=True,
    )
    date: date
    recipient: Optional[str] = Field(default=None)
    hospital: Optional[str] = Field(default=None)
    blood_group: str
    status: str = Field(default="Completed")
    created_at: datetime = Field(default_factory=utc_now)


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    type: str
    title: str
    body: str = Field(sa_column=Column(Text))
    data: Optional[str] = Field(default=None, sa_column=Column(Text))
    is_read: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    expires_at: datetime
    revoked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utc_now)


class EmailVerification(SQLModel, table=True):
    __tablename__ = "email_verifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    email: str = Field(index=True)
    code_hash: str = Field(index=True)
    expires_at: datetime
    consumed: bool = Field(default=False)
    consumed_at: Optional[datetime] = Field(default=None)
    attempts: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id")
    action: str
    entity: str
    entity_id: Optional[str] = Field(default=None)
    metadata_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)


class UserLocation(SQLModel, table=True):
    __tablename__ = "user_locations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    latitude: float
    longitude: float
    recorded_at: datetime = Field(default_factory=utc_now)


class ConversationHistory(SQLModel, table=True):
    __tablename__ = "conversation_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    role: str
    content: str = Field(sa_column=Column(Text))
    tool_name: Optional[str] = Field(default=None)
    tool_payload: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)
