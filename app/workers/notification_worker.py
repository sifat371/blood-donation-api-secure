"""Dedicated durable notification worker entry point."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from uuid import uuid4

from sqlmodel import Session

import app.db.database as database_module
from app.core.config import settings
from app.core.time import utc_now
from app.db.models import (
    DeliveryStatus,
    NotificationDelivery,
    OutboxEvent,
    OutboxStatus,
)
from app.workers.delivery_processor import process_delivery, retry_delay_seconds
from app.workers.outbox_processor import process_outbox_event
from app.workers.queue_claims import claim_deliveries, claim_outbox_events


@dataclass(frozen=True)
class WorkerBatchResult:
    outbox_claimed: int = 0
    outbox_processed: int = 0
    deliveries_claimed: int = 0
    deliveries_processed: int = 0

    @property
    def did_work(self) -> bool:
        return bool(self.outbox_claimed or self.deliveries_claimed)


def _worker_id() -> str:
    configured = settings.notification_worker_id.strip()
    if configured:
        return configured
    return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"


def record_outbox_failure(
    session: Session,
    event_id: int,
    exc: Exception,
    *,
    jitter: float | None = None,
) -> None:
    """Requeue or dead-letter one failed domain event with sanitized error data."""
    event = session.get(OutboxEvent, event_id)
    if event is None:
        return
    now = utc_now()
    event.attempts += 1
    event.last_error = type(exc).__name__
    event.locked_at = None
    event.locked_by = None
    event.updated_at = now
    if event.attempts >= max(settings.notification_worker_max_attempts, 1):
        event.status = OutboxStatus.DEAD.value
    else:
        event.status = OutboxStatus.PENDING.value
        event.available_at = now + __import__("datetime").timedelta(
            seconds=retry_delay_seconds(event.attempts, jitter=jitter)
        )
    session.add(event)
    session.commit()


def record_delivery_worker_failure(
    session: Session,
    delivery_id: int,
    exc: Exception,
    *,
    jitter: float | None = None,
) -> None:
    """Recover from unexpected worker/process failures outside provider handling."""
    delivery = session.get(NotificationDelivery, delivery_id)
    if delivery is None:
        return
    if delivery.status in {
        DeliveryStatus.DELIVERED.value,
        DeliveryStatus.DEAD.value,
        DeliveryStatus.SKIPPED.value,
    }:
        return
    now = utc_now()
    delivery.attempts += 1
    delivery.last_error = type(exc).__name__
    delivery.last_error_category = "worker_error"
    delivery.locked_at = None
    delivery.locked_by = None
    delivery.updated_at = now
    if delivery.attempts >= max(settings.notification_worker_max_attempts, 1):
        delivery.status = DeliveryStatus.DEAD.value
    else:
        delivery.status = DeliveryStatus.RETRY.value
        delivery.available_at = now + __import__("datetime").timedelta(
            seconds=retry_delay_seconds(delivery.attempts, jitter=jitter)
        )
    session.add(delivery)
    session.commit()


def run_once(worker_id: str | None = None) -> WorkerBatchResult:
    """Claim and process one outbox batch followed by one delivery batch."""
    worker_id = worker_id or _worker_id()
    batch_size = max(settings.notification_worker_batch_size, 1)
    lease_seconds = max(settings.notification_worker_lease_seconds, 1)

    with Session(database_module.engine) as claim_session:
        outbox_ids = claim_outbox_events(
            claim_session,
            worker_id,
            batch_size,
            utc_now(),
            lease_seconds=lease_seconds,
        )

    outbox_processed = 0
    for event_id in outbox_ids:
        with Session(database_module.engine) as session:
            try:
                process_outbox_event(session, event_id)
            except Exception as exc:  # isolate one poison event from the batch
                session.rollback()
                record_outbox_failure(session, event_id, exc)
            outbox_processed += 1

    with Session(database_module.engine) as claim_session:
        delivery_ids = claim_deliveries(
            claim_session,
            worker_id,
            batch_size,
            utc_now(),
            lease_seconds=lease_seconds,
        )

    deliveries_processed = 0
    for delivery_id in delivery_ids:
        with Session(database_module.engine) as session:
            try:
                process_delivery(session, delivery_id)
            except Exception as exc:  # DB/programming failure outside provider state machine
                session.rollback()
                record_delivery_worker_failure(session, delivery_id, exc)
            deliveries_processed += 1

    return WorkerBatchResult(
        outbox_claimed=len(outbox_ids),
        outbox_processed=outbox_processed,
        deliveries_claimed=len(delivery_ids),
        deliveries_processed=deliveries_processed,
    )


def run_forever(worker_id: str | None = None) -> None:
    """Poll durable queues until interrupted."""
    worker_id = worker_id or _worker_id()
    while True:
        result = run_once(worker_id)
        if not result.did_work:
            time.sleep(max(settings.notification_worker_poll_seconds, 0.1))


def main() -> None:
    try:
        run_forever()
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
