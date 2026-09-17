"""P3.1 worker observability must be useful without leaking notification data."""

import logging
from datetime import timedelta

from app.core.time import utc_now
from app.db.models import OutboxEvent, OutboxStatus


def test_worker_batch_emits_safe_summary(engine, monkeypatch, caplog):
    import app.workers.notification_worker as worker

    monkeypatch.setattr(worker, "claim_outbox_events", lambda *a, **k: [11])
    monkeypatch.setattr(worker, "claim_deliveries", lambda *a, **k: [22])
    monkeypatch.setattr(worker, "process_outbox_event", lambda *a, **k: None)
    monkeypatch.setattr(worker, "process_delivery", lambda *a, **k: None)

    with caplog.at_level(logging.INFO):
        result = worker.run_once("worker-observe")

    assert result.outbox_claimed == 1
    assert result.deliveries_claimed == 1
    text = caplog.text
    assert "notification_worker_batch" in text
    assert "outbox_claimed=1" in text
    assert "deliveries_claimed=1" in text
    assert "worker-observe" in text


def test_outbox_failure_log_is_sanitized(session, monkeypatch, caplog):
    import app.workers.notification_worker as worker

    now = utc_now()
    row = OutboxEvent(
        event_type="blood_request_created",
        aggregate_type="blood_request",
        aggregate_id="7",
        payload_json='{"patient_name":"SECRET PATIENT"}',
        idempotency_key="observe:7",
        status=OutboxStatus.PROCESSING.value,
        locked_at=now - timedelta(seconds=1),
        locked_by="worker-observe",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    monkeypatch.setattr(worker.settings, "notification_worker_max_attempts", 2)
    monkeypatch.setattr(worker.settings, "notification_worker_retry_seconds", [1, 1])

    with caplog.at_level(logging.WARNING):
        worker.record_outbox_failure(
            session,
            row.id,
            RuntimeError("SECRET PATIENT sensitive-provider-message"),
            jitter=0.0,
        )

    assert "outbox_retry" in caplog.text
    assert f"event_id={row.id}" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "SECRET PATIENT" not in caplog.text
    assert "sensitive-provider-message" not in caplog.text
