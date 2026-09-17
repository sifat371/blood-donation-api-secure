"""Strict schemas for untrusted AI/MCP tool arguments."""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from app.db.models import BloodGroup
from app.schemas.blood_request import MAX_REQUEST_UNITS, MIN_REQUEST_UNITS

MCP_MAX_RESULTS = 20


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class EmptyArgs(ToolArgs):
    pass


class FindDonorsArgs(ToolArgs):
    blood_group: BloodGroup
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=20, ge=1, le=100)
    division: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, max_length=100)
    upazila: Optional[str] = Field(default=None, max_length=100)


class CreateBloodRequestArgs(ToolArgs):
    patient_name: str = Field(min_length=1, max_length=120)
    blood_group: BloodGroup
    units: int = Field(
        default=MIN_REQUEST_UNITS, ge=MIN_REQUEST_UNITS, le=MAX_REQUEST_UNITS
    )
    hospital_name: str = Field(min_length=1, max_length=200)
    hospital_address: Optional[str] = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    needed_date: date
    contact_number: str = Field(min_length=1, max_length=30)
    notes: Optional[str] = None


class UpdateRequestArgs(ToolArgs):
    request_id: int = Field(gt=0)
    action: Literal["accept", "withdraw", "cancel", "complete"]


class ConfirmDonationArgs(ToolArgs):
    request_id: int = Field(gt=0)
    commitment_id: int = Field(gt=0)


class NearbyRequestsArgs(ToolArgs):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=20, ge=1, le=100)


class UpdateAvailabilityArgs(ToolArgs):
    is_available: StrictBool


class UpdateUserLocationArgs(ToolArgs):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


TOOL_ARG_MODELS: dict[str, type[ToolArgs]] = {
    "FindDonors": FindDonorsArgs,
    "CreateBloodRequest": CreateBloodRequestArgs,
    "UpdateRequest": UpdateRequestArgs,
    "ConfirmDonation": ConfirmDonationArgs,
    "GetDonationHistory": EmptyArgs,
    "CheckEligibility": EmptyArgs,
    "GetNearbyRequests": NearbyRequestsArgs,
    "UpdateAvailability": UpdateAvailabilityArgs,
    "GetUserProfile": EmptyArgs,
    "UpdateUserLocation": UpdateUserLocationArgs,
    "GetNotifications": EmptyArgs,
}


def validate_tool_args(tool_name: str, args: dict) -> dict:
    """Validate one tool call and return normalized Python values."""
    model = TOOL_ARG_MODELS.get(tool_name)
    if model is None:
        return args
    validated = model.model_validate(args)
    return validated.model_dump(exclude_none=True)


def format_tool_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
        for err in exc.errors()
    )
