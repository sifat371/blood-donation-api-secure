"""Short-lived durable queue claims for notification workers.

PostgreSQL uses row locks with SKIP LOCKED so multiple workers can claim disjoint
batches. SQLite intentionally omits SKIP LOCKED and is supported for a single
local/test worker only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import (
    DeliveryStatus,
    NotificationDelivery,
    OutboxEvent,
    OutboxStatus,
)

logger = logging.getLogger(__name__)


def _lease_seconds(value: int | None) -> int:
    return max(value if value is not None else settings.notification_worker_lease_seconds, 1)


def claim_outbox_events(
    session: Session,
    worker_id: str,
    limit: int,
    now: datetime,
    *,
    lease_seconds: int | None = None,
) -> list[int]:
    """Claim due/reclaimable domain outbox rows and return detached row ids."""
    cutoff = now - timedelta(seconds=_lease_seconds(lease_seconds))
    due = or_(
        and_(
            OutboxEvent.status == OutboxStatus.PENDING.value,
            OutboxEvent.available_at <= now,
        ),
        and_(
            OutboxEvent.status == OutboxStatus.PROCESSING.value,
            or_(OutboxEvent.locked_at.is_(None), OutboxEvent.locked_at < cutoff),
        ),
    )
    stmt = (
        select(OutboxEvent)
        .where(due)
        .order_by(OutboxEvent.available_at, OutboxEvent.id)
        .limit(max(limit, 0))
    )
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    rows = list(session.exec(stmt).all())
    reclaimed = sum(
        1 for row in rows if row.status == OutboxStatus.PROCESSING.value
    )
    for row in rows:
        row.status = OutboxStatus.PROCESSING.value
        row.locked_at = now
        row.locked_by = worker_id
        row.updated_at = now
        session.add(row)
    session.commit()
    if reclaimed:
        logger.warning(
            "notification_stale_locks_reclaimed queue=outbox worker_id=%s count=%s",
            worker_id,
            reclaimed,
        )
    return [row.id for row in rows]


def claim_deliveries(
    session: Session,
    worker_id: str,
    limit: int,
    now: datetime,
    *,
    lease_seconds: int | None = None,
) -> list[int]:
    """Claim due/reclaimable device delivery rows and return detached row ids."""
    cutoff = now - timedelta(seconds=_lease_seconds(lease_seconds))
    due = or_(
        and_(
            NotificationDelivery.status.in_(
                (DeliveryStatus.PENDING.value, DeliveryStatus.RETRY.value)
            ),
            NotificationDelivery.available_at <= now,
        ),
        and_(
            NotificationDelivery.status == DeliveryStatus.PROCESSING.value,
            or_(
                NotificationDelivery.locked_at.is_(None),
                NotificationDelivery.locked_at < cutoff,
            ),
        ),
    )
    stmt = (
        select(NotificationDelivery)
        .where(due)
        .order_by(NotificationDelivery.available_at, NotificationDelivery.id)
        .limit(max(limit, 0))
    )
    if session.get_bind().dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    rows = list(session.exec(stmt).all())
    reclaimed = sum(
        1 for row in rows if row.status == DeliveryStatus.PROCESSING.value
    )
    for row in rows:
        row.status = DeliveryStatus.PROCESSING.value
        row.locked_at = now
        row.locked_by = worker_id
        row.updated_at = now
        session.add(row)
    session.commit()
    if reclaimed:
        logger.warning(
            "notification_stale_locks_reclaimed queue=delivery worker_id=%s count=%s",
            worker_id,
            reclaimed,
        )
    return [row.id for row in rows]
