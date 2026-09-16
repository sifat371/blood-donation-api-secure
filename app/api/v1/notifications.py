"""
Notification endpoints (§2.6)

GET  /notifications          — paginated, filterable by is_read
POST /notifications/{id}/read — mark as read
"""

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from app.core.deps import CurrentUser, DbSession
from app.db.models import Notification
from app.schemas.notification import NotificationResponse
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=PaginatedResponse[NotificationResponse])
def list_notifications(
    user: CurrentUser,
    session: DbSession,
    is_read: bool | None = Query(None, description="Filter: True=read, False=unread, None=all"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if is_read is not None:
        stmt = stmt.where(Notification.is_read == is_read)
    stmt = stmt.order_by(Notification.created_at.desc())

    # Count
    count_stmt = select(Notification).where(Notification.user_id == user.id)
    if is_read is not None:
        count_stmt = count_stmt.where(Notification.is_read == is_read)
    total = len(session.exec(count_stmt).all())

    items = session.exec(stmt.offset(offset).limit(limit)).all()

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_read(notification_id: int, user: CurrentUser, session: DbSession):
    notification = session.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    session.add(notification)
    session.commit()
    session.refresh(notification)
    return notification
