"""
Chat endpoints (§5)

POST /chat/message  — send a message, get AI response
GET  /chat/history  — conversation history
"""

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse, ChatHistoryResponse
from app.services.chat_agent import process_chat_message, get_chat_history

router = APIRouter(prefix="/chat", tags=["AI Chat"])


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(
    body: ChatMessageRequest,
    user: CurrentUser,
    session: DbSession,
):
    """Send a message to the AI chat agent and get a response."""
    response_text = await process_chat_message(session, user, body.message)
    return ChatMessageResponse(
        role="assistant",
        content=response_text,
    )


@router.get("/history", response_model=ChatHistoryResponse)
def chat_history(user: CurrentUser, session: DbSession):
    """Get conversation history for the current user."""
    messages = get_chat_history(session, user.id)
    return ChatHistoryResponse(
        messages=[
            ChatMessageResponse(
                role=m["role"],
                content=m["content"],
                tool_name=m.get("tool_name"),
            )
            for m in messages
        ]
    )
