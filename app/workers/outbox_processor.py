"""Process durable domain outbox events into notification rows."""

import logging

from sqlmodel import Session

from app.core.time import utc_now
from app.db.models import OutboxEvent, OutboxStatus
from app.services.notification_fanout import materialize_blood_request_created

logger = logging.getLogger(__name__)


class OutboxProcessingError(RuntimeError):
    """Raised when an outbox event cannot be dispatched or decoded."""


def process_outbox_event(session: Session, event_id: int) -> None:
    """Process one outbox event transactionally and idempotently."""
    event = session.get(OutboxEvent, event_id)
    if event is None:
        raise OutboxProcessingError("Outbox event not found")
    if event.status == OutboxStatus.COMPLETED.value:
        return

    try:
        if event.event_type == "blood_request_created":
            materialize_blood_request_created(session, event)
        else:
            raise OutboxProcessingError(
                f"Unsupported outbox event type: {event.event_type}"
            )

        now = utc_now()
        event.status = OutboxStatus.COMPLETED.value
        event.completed_at = now
        event.updated_at = now
        event.locked_at = None
        event.locked_by = None
        session.add(event)
        session.commit()
        logger.info(
            "notification_outbox_completed event_id=%s event_type=%s attempts=%s",
            event.id,
            event.event_type,
            event.attempts,
        )
    except Exception:
        session.rollback()
        raise
