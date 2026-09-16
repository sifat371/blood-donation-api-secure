"""Pydantic v2 schemas — donor search."""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class DonorSearchParams(BaseModel):
    blood_group: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=20, ge=1, le=200)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class DonorResponse(BaseModel):
    id: int
    name: str
    distance_km: float
    blood_group: str
    last_donation_date: Optional[date] = None
    is_available: bool
    division: Optional[str] = None
    district: Optional[str] = None
    upazila: Optional[str] = None

    model_config = {"from_attributes": True}
