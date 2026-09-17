"""Pydantic v2 schemas — notifications."""

import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


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
    data: Optional[dict[str, Any]] = None
    is_read: bool
    created_at: datetime

    @field_validator("data", mode="before")
    @classmethod
    def parse_persisted_json(cls, value):
        if value is None or isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        return decoded if isinstance(decoded, dict) else None

    model_config = {"from_attributes": True}
