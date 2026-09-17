"""Durable domain outbox producers."""

import json

from sqlmodel import Session, select

from app.db.models import OutboxEvent


def enqueue_outbox_event(
    session: Session,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    idempotency_key: str,
) -> OutboxEvent:
    """Persist one idempotent domain event inside the caller-owned transaction."""
    existing = session.exec(
        select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key)
    ).first()
    if existing is not None:
        return existing

    event = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload_json=json.dumps(payload, separators=(",", ":"), sort_keys=True),
        idempotency_key=idempotency_key,
    )
    session.add(event)
    session.flush()
    return event
