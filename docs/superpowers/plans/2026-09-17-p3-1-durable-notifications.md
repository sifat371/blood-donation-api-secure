# P3.1 Durable Notifications & Device Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace best-effort notification push delivery and FastAPI background donor fan-out with a durable PostgreSQL-backed outbox, multi-device FCM installations, per-device delivery tracking, bounded retries, and a dedicated worker while preserving the existing in-app notification API and P2 request lifecycle semantics.

**Architecture:** Keep `Notification` as the user-facing inbox. Use `OutboxEvent` for durable domain work whose recipients are not yet materialized and `NotificationDelivery` for one durable push job per notification/device installation. Business transactions create inbox/outbox/delivery state atomically; a separate worker claims due rows, materializes fan-out, sends FCM, retries transient failures, disables invalid installations, and dead-letters terminal work.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLAlchemy, Alembic, PostgreSQL 16 production/CI, SQLite local/test single-worker mode, Firebase Admin FCM, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-p3-1-durable-notifications-design.md`

## Global Constraints

- No Redis, Celery, Kafka, or external queue.
- PostgreSQL is the production coordination mechanism; SQLite supports one notification worker only.
- Delivery semantics are at-least-once, never claimed as exactly-once.
- The API never waits for FCM and business success never depends on FCM availability.
- `Notification` remains the inbox/source of notification content; queue rows are operational state.
- `device_id` is required for new FCM registrations.
- Every push contains `notification_id`, `event_id`, `type`, and existing deep-link fields.
- A pending delivery must never follow an FCM token into another user's account.
- Existing notifications and FCM rows must survive migration; no historical delivery rows are fabricated.
- Do not log raw FCM tokens, secrets, phone numbers, patient-sensitive payloads, or exact coordinates.
- Startup remains schema-read-only and continues to require Alembic at head.
- Use TDD for every behavior change and keep commits task-scoped.

---

## File Structure

**Database / models**
- Create `alembic/versions/0003_notification_outbox.py`: data-preserving P3.1 schema migration.
- Modify `app/db/models.py`: `OutboxEvent`, `NotificationDelivery`, queue status enums, installation fields, notification identifiers.

**Services**
- Create `app/services/device_service.py`: register/rotate/transfer/unregister installations and skip unresolved jobs safely.
- Refactor `app/services/notifications.py`: create inbox rows and snapshot delivery jobs; remove direct FCM network delivery from request paths.
- Create `app/services/outbox_service.py`: enqueue idempotent domain events.
- Create `app/services/notification_fanout.py`: deterministic donor fan-out materialization from `blood_request_created`.

**Worker**
- Create `app/workers/__init__.py`.
- Create `app/workers/queue_claims.py`: claim/reclaim due rows with PostgreSQL `FOR UPDATE SKIP LOCKED` and SQLite single-worker behavior.
- Create `app/workers/outbox_processor.py`: process claimed domain events.
- Create `app/workers/delivery_processor.py`: send FCM, classify failures, retry/dead-letter/disable devices.
- Create `app/workers/notification_worker.py`: polling/orchestration entry point only.

**API / schemas / config**
- Modify `app/schemas/notification.py`: required `device_id`, response `event_id`.
- Modify `app/api/v1/profile.py`: delegate device registration and add unregister route.
- Modify `app/api/v1/blood_requests.py`: remove `BackgroundTasks`; enqueue fan-out in request transaction.
- Modify `app/services/commitment_service.py` and `app/services/request_service.py`: stop synchronous push calls and rely on delivery rows.
- Modify `app/core/config.py` and `.env.example`: worker/retry settings.

**Tests / CI / docs**
- Extend `tests/test_alembic_migrations.py`.
- Extend `tests/test_api_notifications.py`.
- Create `tests/test_device_service.py`.
- Create `tests/test_notification_outbox.py`.
- Create `tests/test_notification_delivery.py`.
- Extend `tests/test_postgres_integration.py`.
- Modify `.github/workflows/tests.yml`.
- Modify `README.md` and `docs/database-migrations.md` with worker/deployment operations.

---

### Task 1: P3.1 schema migration and runtime models

**Files:**
- Create: `alembic/versions/0003_notification_outbox.py`
- Modify: `app/db/models.py`
- Modify: `tests/test_alembic_migrations.py`

**Interfaces:**
- Produces enums `OutboxStatus` and `DeliveryStatus`.
- Produces models `OutboxEvent` and `NotificationDelivery`.
- Extends `Notification` with `event_id: str` and `dedupe_key: str | None`.
- Extends `FCMToken` with nullable `token`, required `device_id`, active/last-seen/update/disable/failure fields.
- Revision ID is exactly `0003_notification_outbox`, down revision `0002_multi_donor`.

- [ ] **Step 1: Add failing migration tests for the new schema and preservation rules**

Add tests that first upgrade a temporary SQLite database to `0002_multi_donor`, seed one notification and two FCM token rows, then upgrade to head and assert:

```python
assert revision == "0003_notification_outbox"
assert {"outbox_events", "notification_deliveries"} <= set(inspector.get_table_names())
assert {"event_id", "dedupe_key"} <= notification_columns
assert {"device_id", "is_active", "last_seen_at", "updated_at", "disabled_at", "last_failure_reason"} <= token_columns
assert preserved_tokens == ["legacy-token-a", "legacy-token-b"]
assert len(set(event_ids)) == len(event_ids)
assert all(device_id.startswith("legacy-") for device_id in legacy_device_ids)
```

Also assert unique constraints for `(user_id, device_id)` and `(notification_id, fcm_token_id)` and that `token` may be `NULL` after the revision.

- [ ] **Step 2: Run the migration tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_alembic_migrations.py -k notification_outbox
```

Expected: FAIL because revision `0003_notification_outbox`, new columns, and new tables do not exist.

- [ ] **Step 3: Implement `0003_notification_outbox`**

Use explicit Alembic DDL. Backfill existing notification `event_id` values in Python or dialect-neutral SQL with unique UUID strings. Backfill existing FCM rows with deterministic `legacy-<id>` device IDs, mark them active, and preserve tokens. Create indexes needed for due-row scans (`status`, `available_at`) and ownership lookup.

The downgrade must explicitly refuse if lossless reversal is not possible:

```python
def downgrade() -> None:
    raise RuntimeError(
        "0003_notification_outbox is intentionally not losslessly downgradeable: "
        "multi-device installations and delivery/outbox history would be discarded"
    )
```

- [ ] **Step 4: Add matching SQLModel definitions**

Define status enums and models with the exact database constraints. Use `uuid4().hex` or `str(uuid4())` through a small default factory for new `Notification.event_id` values; keep the database migration responsible for historical backfill.

- [ ] **Step 5: Run migration/model regression tests**

Run:

```bash
uv run pytest -q tests/test_alembic_migrations.py
uv run pytest -q tests/test_p2_invariants.py tests/test_p2_privacy_invariants.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add alembic/versions/0003_notification_outbox.py app/db/models.py tests/test_alembic_migrations.py
git commit -m "feat: add durable notification schema"
```

---

### Task 2: Multi-device installation lifecycle and ownership safety

**Files:**
- Create: `app/services/device_service.py`
- Modify: `app/schemas/notification.py`
- Modify: `app/api/v1/profile.py`
- Create: `tests/test_device_service.py`
- Extend: `tests/test_api_notifications.py`

**Interfaces:**
- Produces `register_device(session: Session, user_id: int, device_id: str, fcm_token: str, device_info: str) -> FCMToken`.
- Produces `unregister_device(session: Session, user_id: int, device_id: str) -> FCMToken`.
- Internal helper `skip_unresolved_deliveries(session, fcm_token_id: int, reason: str) -> int` marks only `Pending`, `Retry`, or stale `Processing` jobs `Skipped`.
- API body becomes `FCMTokenUpsertRequest(device_id: str, fcm_token: str, device_info: str = "android")`.

- [ ] **Step 1: Write failing service/API tests**

Cover:

```python
def test_two_devices_can_coexist_for_one_user(...): ...
def test_same_device_token_rotation_updates_only_that_installation(...): ...
def test_token_transfer_clears_old_installation_and_skips_old_pending_jobs(...): ...
def test_unregister_is_404_for_non_owned_device(...): ...
def test_unregister_preserves_delivered_history(...): ...
def test_device_id_is_required(...): ...
```

The token-transfer assertion must prove the safety invariant:

```python
assert old_installation.token is None
assert old_installation.is_active is False
assert old_delivery.status == DeliveryStatus.SKIPPED.value
assert new_installation.user_id == new_user.id
assert new_installation.token == shared_token
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest -q tests/test_device_service.py tests/test_api_notifications.py -k "device or fcm_token or unregister"
```

Expected: FAIL because device-aware service/routes do not exist.

- [ ] **Step 3: Implement device registration/transfer logic**

Registration transaction order must avoid the globally unique token constraint:

```python
conflict = session.exec(select(FCMToken).where(FCMToken.token == fcm_token)).first()
if conflict is not None and (conflict.user_id != user_id or conflict.device_id != device_id):
    skip_unresolved_deliveries(session, conflict.id, "token_transferred")
    conflict.token = None
    conflict.is_active = False
    conflict.disabled_at = now
    conflict.updated_at = now
    session.add(conflict)
    session.flush()
```

Then upsert `(user_id, device_id)`, set the new token, reactivate it, clear stale failure state, and commit once.

- [ ] **Step 4: Refactor profile routes to delegate to the service**

Keep `POST /profile/fcm-token`; add:

```python
@router.delete("/fcm-token/{device_id}")
def unregister_fcm_token(device_id: str, user: CurrentUser, session: DbSession):
    device_service.unregister_device(session, user.id, device_id)
    return {"message": "FCM device unregistered successfully."}
```

Missing/non-owned device returns 404.

- [ ] **Step 5: Run device/API tests**

```bash
uv run pytest -q tests/test_device_service.py tests/test_api_notifications.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add app/services/device_service.py app/schemas/notification.py app/api/v1/profile.py tests/test_device_service.py tests/test_api_notifications.py
git commit -m "feat: support multi-device notification installations"
```

---

### Task 3: Transactional inbox + per-device delivery creation

**Files:**
- Refactor: `app/services/notifications.py`
- Modify: `app/services/commitment_service.py`
- Modify: `app/services/request_service.py`
- Extend: `tests/test_api_notifications.py`
- Create: `tests/test_notification_delivery.py`

**Interfaces:**
- `create_notification(session, user_id, notification_type, title, body, data=None, *, dedupe_key=None, commit=True) -> Notification`.
- New notifications snapshot all active device rows into `NotificationDelivery` rows before commit.
- No request/lifecycle path calls Firebase directly.
- If `dedupe_key` already exists, return the existing notification without creating duplicate delivery rows.

- [ ] **Step 1: Write failing transactional tests**

Cover all of these invariants:

```python
def test_notification_creates_one_delivery_per_active_device(...): ...
def test_inactive_devices_receive_no_new_delivery(...): ...
def test_zero_device_user_still_gets_inbox_notification(...): ...
def test_rollback_removes_notification_and_deliveries(...): ...
def test_same_dedupe_key_returns_same_notification_without_duplicate_jobs(...): ...
def test_accept_and_confirm_make_no_synchronous_fcm_call(...): ...
```

Use `monkeypatch` to make `messaging.send` raise if called from an API action; accept/confirm/cancel must still pass without touching it.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest -q tests/test_notification_delivery.py tests/test_api_notifications.py -k "delivery or rollback or dedupe or synchronous"
```

Expected: FAIL because `NotificationDelivery` rows are not created by `create_notification` and direct send calls still exist.

- [ ] **Step 3: Refactor `create_notification` into a transaction-only producer**

Pseudo-shape:

```python
def create_notification(..., dedupe_key: str | None = None, commit: bool = True) -> Notification:
    if dedupe_key:
        existing = session.exec(select(Notification).where(Notification.dedupe_key == dedupe_key)).first()
        if existing is not None:
            return existing

    notification = Notification(..., dedupe_key=dedupe_key)
    session.add(notification)
    session.flush()

    devices = session.exec(select(FCMToken).where(
        FCMToken.user_id == user_id,
        FCMToken.is_active == True,
        FCMToken.token.is_not(None),
    )).all()
    for device in devices:
        session.add(NotificationDelivery(notification_id=notification.id, fcm_token_id=device.id))
    session.flush()
    if commit:
        session.commit()
        session.refresh(notification)
    return notification
```

Remove `send_push`, `send_notification_push`, and the best-effort loop from business-facing notification creation.

- [ ] **Step 4: Update lifecycle callers**

In `commitment_service.py` and `request_service.py`, remove `send_notification_push` imports, remove post-commit network calls, and let `create_notification(..., commit=False)` create inbox + delivery rows inside the existing business transaction.

- [ ] **Step 5: Run lifecycle regressions**

```bash
uv run pytest -q tests/test_notification_delivery.py tests/test_api_notifications.py tests/test_commitment_service.py tests/test_api_blood_requests.py
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add app/services/notifications.py app/services/commitment_service.py app/services/request_service.py tests/test_notification_delivery.py tests/test_api_notifications.py
git commit -m "feat: persist notification deliveries transactionally"
```

---

### Task 4: Durable request fan-out via domain outbox

**Files:**
- Create: `app/services/outbox_service.py`
- Create: `app/services/notification_fanout.py`
- Create: `app/workers/outbox_processor.py`
- Modify: `app/api/v1/blood_requests.py`
- Modify: `app/services/request_service.py` only to expose/reuse donor-selection rules if needed; do not duplicate P2 compatibility logic.
- Create: `tests/test_notification_outbox.py`
- Extend: `tests/test_api_blood_requests.py`

**Interfaces:**
- `enqueue_outbox_event(session, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict, idempotency_key: str) -> OutboxEvent`.
- `materialize_blood_request_created(session: Session, event: OutboxEvent) -> int` returns count of newly materialized inbox notifications.
- Fan-out notification dedupe key: `blood_request_created:<request_id>:donor:<user_id>`.
- `process_outbox_event(session: Session, event_id: int) -> None` handles the initial `blood_request_created` event type and marks it completed in the same transaction as materialization.

- [ ] **Step 1: Write failing request/outbox tests**

Cover:

```python
def test_request_creation_persists_one_outbox_event_atomically(...): ...
def test_request_creation_no_longer_uses_background_tasks(...): ...
def test_replayed_fanout_event_does_not_duplicate_inbox_rows(...): ...
def test_replayed_fanout_event_does_not_duplicate_delivery_rows(...): ...
def test_fanout_noops_if_request_is_cancelled_or_no_longer_open(...): ...
def test_fanout_uses_compatible_eligible_available_nearby_donors_only(...): ...
```

Assert exact idempotency key:

```python
assert event.idempotency_key == f"blood_request_created:{request_id}"
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest -q tests/test_notification_outbox.py tests/test_api_blood_requests.py -k "outbox or fanout or background"
```

Expected: FAIL because request creation still schedules `BackgroundTasks` and no outbox producer exists.

- [ ] **Step 3: Implement idempotent outbox enqueue**

`enqueue_outbox_event` must check/return the existing unique idempotency key instead of creating a duplicate row on retries.

- [ ] **Step 4: Move donor materialization into a dedicated service**

Reuse the existing P2 donor candidate rules: `is_available`, compatible RBC group, `is_eligible`, location present, radius `NOTIFY_RADIUS_KM`, nearest-first sort, and `NOTIFY_MAX_DONORS`. For each selected donor call:

```python
create_notification(
    session,
    donor.id,
    NotificationType.NEW_BLOOD_REQUEST,
    title,
    body,
    data={"request_id": request.id, "distance_km": round(dist, 2)},
    dedupe_key=f"blood_request_created:{request.id}:donor:{donor.id}",
    commit=False,
)
```

- [ ] **Step 5: Change request creation transaction**

Remove `BackgroundTasks` from the endpoint signature and atomically add the outbox event before `session.commit()`:

```python
enqueue_outbox_event(
    session,
    "blood_request_created",
    "blood_request",
    str(blood_request.id),
    {"request_id": blood_request.id},
    f"blood_request_created:{blood_request.id}",
)
```

- [ ] **Step 6: Implement outbox event processing and idempotent replay**

The processor loads the event, returns immediately for `Completed`, dispatches supported event types, materializes notifications, then marks `Completed` and commits. Unknown/malformed event types raise a typed processing error for the worker retry/dead-letter layer in Task 6.

- [ ] **Step 7: Run fan-out regressions**

```bash
uv run pytest -q tests/test_notification_outbox.py tests/test_api_blood_requests.py tests/test_api_notifications.py
```

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

```bash
git add app/services/outbox_service.py app/services/notification_fanout.py app/workers/outbox_processor.py app/api/v1/blood_requests.py app/services/request_service.py tests/test_notification_outbox.py tests/test_api_blood_requests.py
git commit -m "feat: make donor notification fanout durable"
```

---

### Task 5: FCM delivery processor, retries, token retirement, and dead-lettering

**Files:**
- Create: `app/workers/delivery_processor.py`
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Extend: `tests/test_notification_delivery.py`

**Interfaces:**
- `build_push_payload(notification: Notification) -> dict[str, str]`.
- `process_delivery(session: Session, delivery_id: int, *, send: Callable | None = None) -> None`.
- `classify_delivery_failure(exc: Exception) -> Literal["permanent_token", "transient", "malformed"]`.
- Config fields: `notification_worker_max_attempts`, retry delays, poll interval, batch size, lease seconds, optional worker ID.

- [ ] **Step 1: Write failing delivery-state tests**

Tests must cover:

```python
def test_success_marks_delivery_delivered_and_records_provider_id(...): ...
def test_transient_failure_schedules_retry_in_future(...): ...
def test_fifth_transient_failure_moves_delivery_dead(...): ...
def test_unregistered_token_disables_device_and_skips_related_unresolved_jobs(...): ...
def test_malformed_notification_payload_dead_letters_without_long_retry(...): ...
def test_missing_firebase_configuration_keeps_work_retryable(...): ...
def test_payload_contains_notification_event_type_and_deep_link_fields(...): ...
def test_delivery_refuses_if_installation_owner_no_longer_matches_notification_owner(...): ...
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
uv run pytest -q tests/test_notification_delivery.py -k "provider or retry or dead or invalid or payload or owner"
```

Expected: FAIL because the delivery processor does not exist.

- [ ] **Step 3: Implement payload building with ownership verification**

Before sending, load `Notification` and `FCMToken` and enforce:

```python
if not device.is_active or not device.token or device.user_id != notification.user_id:
    delivery.status = DeliveryStatus.SKIPPED.value
    delivery.last_error_category = "device_not_deliverable"
    ...
    return
```

Merge required keys over the stored deep-link JSON so clients always receive trusted identifiers from server state.

- [ ] **Step 4: Implement provider send and failure classification**

Use Firebase Admin `messaging.Message` and `messaging.send`. Treat unregistered/invalid installation-token errors as permanent token failures; transport/provider availability failures as transient; invalid local JSON/content construction as malformed. Never log the raw token or notification body.

- [ ] **Step 5: Implement bounded retry with jitter**

Store retry schedule in configuration. For deterministic tests, isolate delay calculation:

```python
def retry_delay_seconds(attempt: int, *, jitter: float = 0.0) -> float:
    base = settings.notification_worker_retry_seconds[attempt - 1]
    return base + jitter
```

Default logical schedule: 60, 300, 1800, 7200, 43200 seconds. Fifth failed attempt is terminal `Dead`.

- [ ] **Step 6: Run delivery tests**

```bash
uv run pytest -q tests/test_notification_delivery.py
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```bash
git add app/workers/delivery_processor.py app/core/config.py .env.example tests/test_notification_delivery.py
git commit -m "feat: add retryable FCM delivery processor"
```

---

### Task 6: Queue claiming, stale-lock recovery, and dedicated worker process

**Files:**
- Create: `app/workers/__init__.py`
- Create: `app/workers/queue_claims.py`
- Modify: `app/workers/outbox_processor.py`
- Modify: `app/workers/delivery_processor.py`
- Create: `app/workers/notification_worker.py`
- Create: `tests/test_notification_worker.py`

**Interfaces:**
- `claim_outbox_events(session, worker_id: str, limit: int, now: datetime) -> list[int]`.
- `claim_deliveries(session, worker_id: str, limit: int, now: datetime) -> list[int]`.
- Claim functions reclaim expired `Processing` leases and return claimed row IDs, not attached ORM objects.
- `run_once(worker_id: str | None = None) -> WorkerBatchResult` processes one outbox batch and one delivery batch.
- CLI loop: `python -m app.workers.notification_worker`.

- [ ] **Step 1: Write failing claim/lease/orchestration tests**

Cover:

```python
def test_claim_marks_due_rows_processing_with_worker_and_lock_time(...): ...
def test_not_due_retry_row_is_not_claimed(...): ...
def test_fresh_processing_lease_is_not_reclaimed(...): ...
def test_stale_processing_lease_is_reclaimed(...): ...
def test_run_once_processes_outbox_before_delivery(...): ...
def test_worker_loop_does_not_crash_when_no_rows_exist(...): ...
```

For SQLite tests, enforce/document single-worker assumptions rather than pretending `SKIP LOCKED` exists.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest -q tests/test_notification_worker.py
```

Expected: FAIL because queue claim/orchestration modules do not exist.

- [ ] **Step 3: Implement dialect-aware claiming**

For PostgreSQL build a due-row select ordered by `available_at, id`, use `.with_for_update(skip_locked=True)`, transition claimed rows to `Processing`, set lease metadata, and commit quickly. For SQLite use the same filtering/state transition without `skip_locked`; document one worker only.

Due predicate includes:

```text
status in (Pending, Retry)
OR (status = Processing AND locked_at < lease_cutoff)
```

Outbox has `Pending/Processing/Completed/Dead`; its retryable failures return to `Pending` with future `available_at`.

- [ ] **Step 4: Add failure accounting around processors**

Worker orchestration catches processing failures, increments attempt count, writes sanitized error category/message, and either requeues or moves to `Dead` according to max attempts. External FCM send remains handled by the delivery processor's delivery-specific state machine.

- [ ] **Step 5: Implement `run_once` and CLI polling loop**

`run_once` opens fresh sessions per claimed item after the short claim transaction so external work never holds claim locks. CLI loop sleeps only when a pass finds no immediately available work; graceful `KeyboardInterrupt` exits cleanly.

- [ ] **Step 6: Run worker/unit regression tests**

```bash
uv run pytest -q tests/test_notification_worker.py tests/test_notification_outbox.py tests/test_notification_delivery.py
uv run python -m compileall -q app tests alembic
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add app/workers tests/test_notification_worker.py
git commit -m "feat: add durable notification worker"
```

---

### Task 7: PostgreSQL concurrent claiming and CI coverage

**Files:**
- Extend: `tests/test_postgres_integration.py`
- Modify: `.github/workflows/tests.yml`

**Interfaces:**
- Uses Task 6 `claim_outbox_events` and `claim_deliveries` exactly as defined.
- PostgreSQL 16 remains the CI service.

- [ ] **Step 1: Add PostgreSQL-only concurrency tests**

Create due rows, then synchronize two worker threads with a `Barrier`. Each worker opens its own SQLModel `Session` and claims the same queue concurrently.

Assertions:

```python
assert set(worker_a_ids).isdisjoint(worker_b_ids)
assert sorted(worker_a_ids + worker_b_ids) == sorted(all_due_ids)
```

Add one equivalent test for `notification_deliveries` and one stale-lease reclaim test proving only one worker obtains a reclaimed row.

- [ ] **Step 2: Run locally and verify expected skip outside PostgreSQL**

```bash
uv run pytest -q tests/test_postgres_integration.py
```

Expected on SQLite/local default: tests SKIP because the module requires PostgreSQL.

- [ ] **Step 3: Add P3.1 suites to the PostgreSQL CI job**

Extend the existing job command to include:

```text
tests/test_notification_outbox.py
tests/test_notification_delivery.py
tests/test_notification_worker.py
tests/test_device_service.py
```

Keep `uv run alembic upgrade head` before tests.

- [ ] **Step 4: Commit Task 7**

```bash
git add tests/test_postgres_integration.py .github/workflows/tests.yml
git commit -m "test: cover notification worker concurrency on postgres"
```

---

### Task 8: Operational docs, cleanup, and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/database-migrations.md`
- Modify: `.env.example` if Task 5 did not already document every worker variable.
- Review/remove obsolete fan-out/direct-push code from `app/services/request_service.py`, `app/services/notifications.py`, and `app/api/v1/blood_requests.py`.
- Review all P3.1 tests and workflow files.

**Interfaces:**
- Document production commands:
  - API process remains existing Uvicorn invocation.
  - Worker process: `uv run python -m app.workers.notification_worker`.
- Document PostgreSQL multi-worker support and SQLite single-worker limitation.
- Document required mobile API change: `device_id` is mandatory on FCM registration and unregister uses `DELETE /api/v1/profile/fcm-token/{device_id}`.

- [ ] **Step 1: Remove obsolete synchronous/background notification paths**

Search the repository and make these commands return no obsolete runtime usages:

```bash
grep -R "send_notification_push" -n app || true
grep -R "notify_nearby_donors_task" -n app || true
grep -R "background_tasks.add_task" -n app/api/v1/blood_requests.py || true
```

Expected: no runtime matches for the old notification delivery/fan-out path.

- [ ] **Step 2: Document deployment and failure semantics**

README/database docs must state:

- run `uv run alembic upgrade head` before API/worker rollout;
- run at least one notification worker in production;
- multiple PostgreSQL workers are safe through `SKIP LOCKED`;
- SQLite is single-worker only;
- business API success is independent of FCM availability;
- inspect `Dead` outbox/delivery rows operationally rather than assuming delivery;
- historical notifications are not retroactively pushed after migration or new device registration.

- [ ] **Step 3: Run compile + focused P3.1 suite**

```bash
uv run python -m compileall -q app tests scripts alembic
uv run pytest -q tests/test_alembic_migrations.py tests/test_device_service.py tests/test_notification_outbox.py tests/test_notification_delivery.py tests/test_notification_worker.py tests/test_api_notifications.py tests/test_api_blood_requests.py tests/test_commitment_service.py
```

Expected: PASS; PostgreSQL-only tests may skip on SQLite as designed.

- [ ] **Step 4: Run the full SQLite suite**

```bash
uv run pytest -q
```

Expected: all tests pass with only understood existing deprecation warnings or newly documented warnings; no P3.1 failure may be waived.

- [ ] **Step 5: Push branch and verify GitHub Actions**

Push `p3.1-durable-notifications`, then verify both `pytest` and `postgres` jobs complete successfully. Inspect PostgreSQL job logs to confirm `0003_notification_outbox` upgrade runs and concurrent claim tests execute rather than skip.

- [ ] **Step 6: Review diff against the approved spec**

Confirm every spec requirement has an implemented/tested path: durable fan-out, multi-device identity, token-transfer safety, inbox/delivery atomicity, per-device retries, dead-letter state, stale-lock recovery, event dedupe, client `event_id`, no synchronous FCM, no `BackgroundTasks` fan-out, PostgreSQL multi-worker claiming, and SQLite single-worker documentation.

- [ ] **Step 7: Commit documentation/cleanup**

```bash
git add README.md docs/database-migrations.md .env.example app tests
git commit -m "docs: document durable notification operations"
```

---

## Plan self-review result

- **Spec coverage:** All approved P3.1 sections map to Tasks 1-8. Durable generation is covered in Task 4; durable device delivery in Tasks 3/5/6; multi-device ownership safety in Task 2; PostgreSQL horizontal safety in Task 7; migration/operations/compatibility in Tasks 1/8.
- **No placeholders:** The plan contains no TBD/TODO implementation gaps; each behavior has concrete interfaces, tests, commands, and expected results.
- **Type/interface consistency:** `OutboxEvent`, `NotificationDelivery`, `create_notification`, device service functions, claim functions, and processor entry points are defined before downstream tasks consume them.
- **Scope:** P3.1 remains limited to durable notifications/device delivery; auth sessions, AI confirmation/rate limiting, trust/moderation, SMS/email, and preference systems remain deferred to later P3 slices.
