"""Pydantic v2 schemas — blood requests."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.db.models import BloodGroup

# ── Single source of truth for the units business rule ───
#
# One blood request covers one patient's immediate need. 1–10 units spans
# everything from a routine transfusion to major surgery / trauma, and is the
# bound enforced everywhere: the create schema below, the MCP CreateBloodRequest
# tool, and the frontend's numeric input.
#
# Deliberately NOT enforced on BloodRequestResponse: re-validating business
# bounds on the way out turns any legacy or out-of-range row into an
# unhandled ResponseValidationError (HTTP 500) instead of a readable 422 at
# the input boundary. Response models describe, they don't gate-keep.
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

    # Validated against the enum, stored as the plain "O+" string. The donor
    # fan-out matches blood_group by string equality, so an unchecked free-text
    # group would create a request no donor could ever be notified about.
    model_config = {"use_enum_values": True}


class BloodRequestResponse(BaseModel):
    id: int
    recipient_id: Optional[int] = None
    patient_name: Optional[str] = None
    blood_group: str
    units: int
    hospital_name: str
    hospital_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    needed_date: date
    contact_number: Optional[str] = None
    notes: Optional[str] = None
    status: str
    accepted_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # Enriched fields (populated via joins)
    recipient_name: Optional[str] = None
    donor_name: Optional[str] = None
    donor_phone: Optional[str] = None
    distance_km: Optional[float] = None

    model_config = {"from_attributes": True}


class NearbyRequestsParams(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=20, ge=1, le=100)
