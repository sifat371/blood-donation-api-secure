"""Pydantic v2 schemas — notifications."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FCMTokenUpsertRequest(BaseModel):
    fcm_token: str
    device_info: Optional[str] = "android"


class NotificationResponse(BaseModel):
    id: int
    user_id: int
    type: str
    title: str
    body: str
    data: Optional[str] = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
