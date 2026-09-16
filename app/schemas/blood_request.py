"""Pydantic v2 schemas — blood requests and donor commitments."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.db.models import BloodGroup
from app.core.time import business_today

MIN_REQUEST_UNITS = 1
MAX_REQUEST_UNITS = 10


class BloodRequestCreate(BaseModel):
    patient_name: str = Field(min_length=1, max_length=120)
    blood_group: BloodGroup
    units: int = Field(
        default=MIN_REQUEST_UNITS, ge=MIN_REQUEST_UNITS, le=MAX_REQUEST_UNITS
    )
    hospital_name: str = Field(min_length=1, max_length=200)
    hospital_address: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    needed_date: date
    contact_number: str = Field(min_length=1, max_length=30)
    notes: Optional[str] = None

    @field_validator("needed_date")
    @classmethod
    def needed_date_cannot_be_in_the_past(cls, value: date) -> date:
        if value < business_today():
            raise ValueError("needed_date cannot be in the past")
        return value

    model_config = {"use_enum_values": True}


class CommitmentResponse(BaseModel):
    id: int
    request_id: int
    donor_id: Optional[int] = None
    status: str
    committed_at: datetime
    completed_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    donor_name: Optional[str] = None
    donor_phone: Optional[str] = None

    model_config = {"from_attributes": True}


class BloodRequestResponse(BaseModel):
    id: int
    recipient_id: Optional[int] = None
    patient_name: Optional[str] = None
    blood_group: str
    units: int
    units_required: int = 0
    units_committed: int = 0
    units_completed: int = 0
    remaining_units: Optional[int] = None
    hospital_name: str
    hospital_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    needed_date: date
    contact_number: Optional[str] = None
    notes: Optional[str] = None
    status: str
    legacy_completion_incomplete: bool = False
    my_commitment_status: Optional[str] = None

    # Deprecated P1 singular-donor compatibility fields. P2 populates these
    # only when exactly one commitment is visible to the viewer.
    accepted_by: Optional[int] = None
    donor_name: Optional[str] = None
    donor_phone: Optional[str] = None

    created_at: datetime
    updated_at: datetime
    recipient_name: Optional[str] = None
    distance_km: Optional[float] = None

    model_config = {"from_attributes": True}


class NearbyRequestsParams(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=20, ge=1, le=100)
