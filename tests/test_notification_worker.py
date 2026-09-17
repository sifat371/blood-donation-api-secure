"""P3.1 durable worker claim, lease, recovery, and orchestration contracts."""

from datetime import timedelta

from sqlmodel import Session

from app.core.time import utc_now
from app.db.models import (
    DeliveryStatus,
    FCMToken,
    Notification,
    NotificationDelivery,
    NotificationType,
    OutboxEvent,
    OutboxStatus,
)


def _event(session, *, status=OutboxStatus.PENDING.value, available_at=None, locked_at=None):
    now = utc_now()
    row = OutboxEvent(
        event_type="test_event",
        aggregate_type="test",
        aggregate_id="1",
        payload_json="{}",
        idempotency_key=f"worker-test:{now.timestamp()}:{id(object())}",
        status=status,
        available_at=available_at or now,
        locked_at=locked_at,
        locked_by="old-worker" if locked_at else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _delivery(session, sample_user, *, status=DeliveryStatus.PENDING.value, available_at=None, locked_at=None):
    now = utc_now()
    device = FCMToken(
        user_id=sample_user.id,
        device_id=f"worker-device-{id(object())}",
        token=f"worker-token-{id(object())}",
        device_info="android",
    )
    session.add(device)
    session.flush()
    notification = Notification(
        user_id=sample_user.id,
        type=NotificationType.PROFILE_REMINDER.value,
        title="Worker test",
        body="body",
    )
    session.add(notification)
    session.flush()
    row = NotificationDelivery(
        notification_id=notification.id,
        fcm_token_id=device.id,
        status=status,
        available_at=available_at or now,
        locked_at=locked_at,
        locked_by="old-worker" if locked_at else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_claim_marks_due_outbox_rows_processing_with_worker_and_lock_time(session):
    from app.workers.queue_claims import claim_outbox_events

    row = _event(session)
    now = utc_now()

    claimed = claim_outbox_events(session, "worker-a", 10, now)

    assert claimed == [row.id]
    session.expire_all()
    stored = session.get(OutboxEvent, row.id)
    assert stored.status == OutboxStatus.PROCESSING.value
    assert stored.locked_by == "worker-a"
    assert stored.locked_at is not None


def test_not_due_retry_delivery_is_not_claimed(session, sample_user):
    from app.workers.queue_claims import claim_deliveries

    row = _delivery(
        session,
        sample_user,
        status=DeliveryStatus.RETRY.value,
        available_at=utc_now() + timedelta(hours=1),
    )

    assert claim_deliveries(session, "worker-a", 10, utc_now()) == []
    session.expire_all()
    assert session.get(NotificationDelivery, row.id).status == DeliveryStatus.RETRY.value


def test_fresh_processing_lease_is_not_reclaimed(session):
    from app.workers.queue_claims import claim_outbox_events

    now = utc_now()
    row = _event(
        session,
        status=OutboxStatus.PROCESSING.value,
        locked_at=now - timedelta(seconds=10),
    )

    assert claim_outbox_events(session, "worker-b", 10, now, lease_seconds=120) == []
    session.expire_all()
    assert session.get(OutboxEvent, row.id).locked_by == "old-worker"


def test_stale_processing_lease_is_reclaimed(session):
    from app.workers.queue_claims import claim_outbox_events

    now = utc_now()
    row = _event(
        session,
        status=OutboxStatus.PROCESSING.value,
        locked_at=now - timedelta(seconds=121),
    )

    claimed = claim_outbox_events(session, "worker-b", 10, now, lease_seconds=120)

    assert claimed == [row.id]
    session.expire_all()
    stored = session.get(OutboxEvent, row.id)
    assert stored.locked_by == "worker-b"
    assert stored.locked_at == now


def test_stale_delivery_lease_is_reclaimed(session, sample_user):
    from app.workers.queue_claims import claim_deliveries

    now = utc_now()
    row = _delivery(
        session,
        sample_user,
        status=DeliveryStatus.PROCESSING.value,
        locked_at=now - timedelta(seconds=121),
    )

    assert claim_deliveries(session, "worker-b", 10, now, lease_seconds=120) == [row.id]
    session.expire_all()
    stored = session.get(NotificationDelivery, row.id)
    assert stored.locked_by == "worker-b"
    assert stored.locked_at == now


def test_run_once_processes_outbox_before_delivery(engine, monkeypatch):
    import app.workers.notification_worker as worker

    calls = []
    monkeypatch.setattr(worker, "claim_outbox_events", lambda *a, **k: [11])
    monkeypatch.setattr(worker, "claim_deliveries", lambda *a, **k: [22])
    monkeypatch.setattr(
        worker,
        "process_outbox_event",
        lambda session, row_id: calls.append(("outbox", row_id)),
    )
    monkeypatch.setattr(
        worker,
        "process_delivery",
        lambda session, row_id: calls.append(("delivery", row_id)),
    )

    result = worker.run_once("worker-test")

    assert calls == [("outbox", 11), ("delivery", 22)]
    assert result.outbox_claimed == 1
    assert result.outbox_processed == 1
    assert result.deliveries_claimed == 1
    assert result.deliveries_processed == 1


def test_run_once_is_harmless_when_no_rows_exist(engine, monkeypatch):
    import app.workers.notification_worker as worker

    monkeypatch.setattr(worker, "claim_outbox_events", lambda *a, **k: [])
    monkeypatch.setattr(worker, "claim_deliveries", lambda *a, **k: [])

    result = worker.run_once("worker-empty")

    assert result.outbox_claimed == 0
    assert result.outbox_processed == 0
    assert result.deliveries_claimed == 0
    assert result.deliveries_processed == 0
    assert result.did_work is False


def test_outbox_processing_failure_requeues_then_dead_letters(session, monkeypatch):
    import app.workers.notification_worker as worker

    row = _event(session, status=OutboxStatus.PROCESSING.value, locked_at=utc_now())
    monkeypatch.setattr(worker.settings, "notification_worker_max_attempts", 2)
    monkeypatch.setattr(worker.settings, "notification_worker_retry_seconds", [1, 1])

    worker.record_outbox_failure(session, row.id, RuntimeError("sensitive details"), jitter=0.0)
    session.expire_all()
    first = session.get(OutboxEvent, row.id)
    assert first.status == OutboxStatus.PENDING.value
    assert first.attempts == 1
    assert first.last_error == "RuntimeError"
    assert first.locked_at is None
    assert first.locked_by is None

    first.status = OutboxStatus.PROCESSING.value
    first.locked_at = utc_now()
    first.locked_by = "worker-a"
    session.add(first)
    session.commit()

    worker.record_outbox_failure(session, row.id, RuntimeError("more sensitive details"), jitter=0.0)
    session.expire_all()
    final = session.get(OutboxEvent, row.id)
    assert final.status == OutboxStatus.DEAD.value
    assert final.attempts == 2
    assert final.last_error == "RuntimeError"
