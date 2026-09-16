"""Pydantic v2 schemas — donation history."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class DonationHistoryResponse(BaseModel):
    id: int
    donor_id: int
    request_id: Optional[int] = None
    date: date
    recipient: Optional[str] = None
    hospital: Optional[str] = None
    blood_group: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
