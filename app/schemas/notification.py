"""Pydantic v2 schemas — notifications."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FCMTokenUpsertRequest(BaseModel):
    device_id: str = Field(min_length=1)
    fcm_token: str = Field(min_length=1)
    device_info: str = "android"


class NotificationResponse(BaseModel):
    id: int
    user_id: int
    event_id: str
    type: str
    title: str
    body: str
    data: Optional[str] = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
