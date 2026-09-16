"""Pydantic v2 schemas — chat."""

from typing import Optional, List

from pydantic import BaseModel


class ChatMessageRequest(BaseModel):
    message: str


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    tool_name: Optional[str] = None
    tool_result: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    messages: List[ChatMessageResponse]
