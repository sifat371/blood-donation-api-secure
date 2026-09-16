# P3.1 Durable Notifications & Device Delivery — Design

Date: 2026-09-17
Branch: `p3.1-durable-notifications`
Base: `main` at `2439d00d1cec1683493fde4cfe7fe5ded53236fb`

## 1. Purpose

P3.1 replaces best-effort push delivery with a durable notification pipeline while preserving the existing in-app inbox and the P2 request/commitment lifecycle.

Goals:

1. Notification-generating business events must survive API-process crashes.
2. FCM delivery work must survive API/worker restarts and transient provider failures.
3. One user may have multiple active app installations without one registration replacing another.
4. Delivery must be observable, retryable, idempotent, and safe under multiple PostgreSQL workers.

P3.1 adds no Redis, Celery, Kafka, or other queue service. PostgreSQL is the production coordination mechanism. SQLite remains supported for local development/tests with a single notification worker only.

## 2. Current-state problems

The current app stores `Notification` rows durably, but FCM is sent best-effort after the business transaction commits. A crash between commit and send can permanently lose the push attempt.

Blood-request creation has a second gap: the request is committed and donor fan-out is then scheduled with FastAPI `BackgroundTasks`. If the process dies after commit, the request can survive while donor notifications are never generated.

FCM registration also effectively behaves as one device per user because `/profile/fcm-token` updates the first token row for that user. It does not model multiple installations or token rotation safely.

## 3. Scope

### In scope

- Transactional DB outbox for recoverable notification-domain work.
- Durable per-device push queue.
- Multi-device installation model.
- Stable client-provided `device_id`.
- Registration, token rotation, token ownership transfer, unregister, and invalid-token retirement.
- Per-device status, attempts, provider result, failure classification, retry, and dead-letter state.
- Stable notification `event_id` for client deduplication.
- Durable donor fan-out for newly created blood requests.
- Dedicated Python notification worker.
- PostgreSQL `FOR UPDATE SKIP LOCKED` claiming and stale-lock reclamation.
- SQLite single-worker fallback.
- Data-preserving Alembic migration.
- Regression coverage for notification privacy, deep links, request lifecycle, and P2 donor-selection rules.

### Out of scope

- Redis/Celery/Kafka.
- WebSockets/realtime inbox streaming.
- Email/SMS channels.
- Per-type notification preferences.
- Auth-session/device-session coupling (P3.2).
- AI/chat rate limiting and action confirmation (P3.3).
- Trust/moderation (P3.4).
- Retrospective push attempts for historical notifications.

## 4. Approved architecture

P3.1 uses two durable queue-like structures with separate responsibilities:

1. `outbox_events` — recoverable domain work where notification recipients are not materialized yet, initially blood-request donor fan-out.
2. `notification_deliveries` — one durable FCM job per notification/device installation.

`Notification` remains the user-facing in-app inbox and the source of truth for notification content. Queue rows are operational state, not business truth.

The API never waits for FCM and never makes business success depend on provider availability.

## 5. Alembic revision and data model

Create revision `0003_notification_outbox`.

### 5.1 `notifications`

Keep the existing primary key and fields. Add:

- `event_id`: non-null, unique UUID-style string.
- `dedupe_key`: nullable, unique string used when a producer has a deterministic business identity for a notification.

Rules:

- Every new notification gets an `event_id` at creation time.
- Existing rows are backfilled with unique generated `event_id` values.
- `event_id` is returned by the API and included in every push payload.
- Fan-out notifications use deterministic `dedupe_key` values so replay cannot create a second inbox row for the same request/donor event.
- Direct lifecycle notifications may also use deterministic keys when a natural business key exists, but `dedupe_key` remains optional.

Initial fan-out key format:

`blood_request_created:<request_id>:donor:<user_id>`

### 5.2 `fcm_tokens`

Evolve the table into installation records.

Existing fields retained: `id`, `user_id`, `token`, `device_info`, `created_at`.

Changes/additions:

- `token` becomes nullable so an old installation can be retained as historical/auditable after its token moves elsewhere.
- `device_id`: non-null stable installation identifier.
- `is_active`: bool, default true.
- `last_seen_at`.
- `updated_at`.
- `disabled_at` nullable.
- `last_failure_reason` nullable.

Constraints:

- unique `(user_id, device_id)`.
- unique `token` for non-null tokens (ordinary unique semantics are sufficient because PostgreSQL and SQLite permit multiple `NULL`s).
- indexed `user_id` and active-device lookup.

Legacy migration:

- Preserve every existing row.
- Assign deterministic IDs such as `legacy-<row_id>`.
- Mark legacy rows active.
- Keep existing non-null token values.

### 5.3 `outbox_events`

Fields:

- `id`.
- `event_type`.
- `aggregate_type`.
- `aggregate_id`.
- `payload_json`.
- `idempotency_key` unique.
- `status`: `Pending`, `Processing`, `Completed`, `Dead`.
- `attempts`.
- `available_at`.
- `locked_at` nullable.
- `locked_by` nullable.
- `last_error` nullable.
- `created_at`.
- `updated_at`.
- `completed_at` nullable.

Initial event:

- `blood_request_created`.

Initial idempotency key:

`blood_request_created:<request_id>`

### 5.4 `notification_deliveries`

Fields:

- `id`.
- `notification_id` FK.
- `fcm_token_id` FK to the installation row.
- `status`: `Pending`, `Processing`, `Delivered`, `Retry`, `Dead`, `Skipped`.
- `attempts`.
- `available_at`.
- `locked_at` nullable.
- `locked_by` nullable.
- `provider_message_id` nullable.
- `last_error` nullable.
- `last_error_category` nullable.
- `delivered_at` nullable.
- `created_at`.
- `updated_at`.

Constraint:

- unique `(notification_id, fcm_token_id)`.

A delivery references an installation identity, not a free-form token string. Token rotation on the same installation may therefore allow a pending delivery to use the installation's current token, but only when the installation is still active and still belongs to the same user as the notification.

## 6. Critical ownership-safety invariant

A pending delivery must never follow an FCM token into another user's account.

Device registration therefore follows these rules:

1. `(authenticated_user_id, device_id)` is the stable installation identity.
2. If the same installation rotates its token, update that installation row.
3. If a submitted token currently belongs to a different installation/user, do **not** simply change ownership of that existing row while leaving old jobs attached.
4. Instead, in one transaction:
   - mark the old installation inactive;
   - set its token to `NULL` and record the reason/time;
   - mark its unresolved (`Pending`, `Retry`, stale `Processing`) delivery rows `Skipped`;
   - attach the token to the authenticated user's target installation, creating or reactivating that installation as needed.
5. Before every send, the delivery processor must verify:
   - installation exists;
   - installation is active;
   - installation token is non-null;
   - `installation.user_id == notification.user_id`.
   If any check fails, mark the delivery `Skipped` and do not call FCM.

This invariant prevents cross-account delivery even if a token is reassigned between notification creation and worker processing.

## 7. Delivery semantics

P3.1 provides at-least-once delivery semantics, not exactly-once delivery.

Exactly-once cannot be guaranteed end-to-end because a provider timeout can occur after FCM accepts a message but before the worker receives the acknowledgement.

Mitigations:

- stable `event_id` and numeric `notification_id` in every push payload;
- unique notification/device delivery rows;
- delivered rows are not intentionally resent;
- retries target unresolved rows only;
- clients can suppress duplicate presentation using `event_id`.

## 8. Business write paths

### 8.1 Direct lifecycle notifications

Examples:

- donor commits -> recipient notification;
- recipient confirms donation -> donor notification;
- request cancellation -> affected donor notifications.

The existing business transaction atomically persists:

- business state change;
- audit rows;
- `Notification` row;
- one `NotificationDelivery` row for every currently active installation belonging to the recipient.

No network call occurs before or during commit.

If the transaction rolls back, none of the notification/delivery rows remain.

If the user has no active device, the in-app notification still exists. Registering a device later does not retroactively enqueue old notifications.

### 8.2 Blood-request donor fan-out

Request creation atomically persists:

- `BloodRequest`;
- audit row;
- `OutboxEvent(event_type='blood_request_created')`.

The endpoint no longer depends on FastAPI `BackgroundTasks` for donor fan-out.

The worker later processes the event by:

1. loading the request;
2. verifying the event is still relevant and the request still has open capacity;
3. applying the same P2 RBC compatibility, eligibility, availability, radius, and recipient-cap rules;
4. selecting recipients deterministically (same sort/ranking rule for every replay);
5. creating each donor's `Notification` with deterministic `dedupe_key`;
6. creating current active-device delivery rows for each newly created or already-existing deduped notification as appropriate;
7. marking the outbox event `Completed` in the **same transaction** that materializes the notification state.

Reprocessing is harmless because:

- the outbox event has a unique idempotency key;
- donor inbox notifications have deterministic unique `dedupe_key` values;
- delivery rows have unique `(notification_id, fcm_token_id)` constraints;
- event completion and notification materialization commit together.

## 9. Worker design

Run a separate Python process, for example:

`uv run python -m app.workers.notification_worker`

The worker performs two independent batches:

1. claim/process due `outbox_events`;
2. claim/send due `notification_deliveries`.

### PostgreSQL claiming

Use short transactions and `SELECT ... FOR UPDATE SKIP LOCKED`.

Claim operation:

- select due rows;
- set `Processing`;
- set `locked_at`;
- set `locked_by` to a worker instance identifier;
- commit the claim quickly before external/network work.

Multiple workers can run concurrently without intentionally claiming the same row.

### SQLite

SQLite is supported for development and ordinary tests with one worker only. Horizontal claim semantics are a PostgreSQL production property and receive PostgreSQL integration tests.

### Stale lock reclamation

`Processing` rows whose `locked_at` is older than the configured lease become reclaimable. Reclamation must not strand jobs or reset attempt accounting incorrectly.

## 10. Retry and terminal-state policy

Default transient-failure schedule:

- about 1 minute;
- about 5 minutes;
- about 30 minutes;
- about 2 hours;
- about 12 hours.

After the fifth failed attempt, move the delivery to `Dead`.

The retry schedule, max attempts, poll interval, batch size, and lease duration are configuration values. Retry timing adds bounded jitter to avoid synchronized retry spikes.

Failure classes:

### Permanent invalid/unregistered token

- deactivate installation;
- clear/make token unusable as appropriate;
- record disable time/reason;
- mark current delivery terminal `Skipped`;
- mark all unresolved delivery rows for that installation `Skipped`.

### Transient provider/network failure

- increment attempts;
- set `Retry`;
- compute future `available_at` from configured backoff plus jitter.

### Malformed/internal payload error

If retry cannot plausibly fix the problem, move to `Dead` immediately or after a very small bounded retry count.

### Firebase unavailable/misconfigured

Business/API actions still succeed. Delivery rows remain unresolved/retryable rather than disappearing.

## 11. Device API contract

### Register/update

Keep:

`POST /api/v1/profile/fcm-token`

New request body:

```json
{
  "device_id": "stable-installation-id",
  "fcm_token": "current-fcm-token",
  "device_info": "android"
}
```

`device_id` is required. This is the only intentional client-facing breaking change in P3.1.

Registration behavior:

- identify target by `(authenticated user, device_id)`;
- create, update, or reactivate only that installation;
- refresh `last_seen_at` and `updated_at`;
- safely handle token reassignment using the ownership-safety invariant above;
- never leave a token simultaneously usable by two installation rows.

### Unregister

Add:

`DELETE /api/v1/profile/fcm-token/{device_id}`

Behavior:

- only affects the authenticated user's installation;
- mark inactive and clear the usable token;
- record disable/update time;
- mark unresolved deliveries for that installation `Skipped`;
- preserve already-delivered history;
- return 404 for missing/non-owned device IDs rather than confirming another user's installation exists.

Global logout-all-devices belongs to P3.2.

## 12. Notification API compatibility

Existing list/read endpoints and ownership/privacy behavior remain unchanged.

`NotificationResponse` adds `event_id`.

Existing numeric `id`, JSON-string `data`, `is_read`, and `created_at` remain.

Every FCM payload includes at least:

- `notification_id`;
- `event_id`;
- notification `type`;
- existing deep-link data such as `request_id`/`commitment_id` when present.

## 13. Service boundaries

Keep responsibilities isolated:

- notification creation service — creates inbox notification and snapshots active-device delivery rows inside a caller-owned transaction;
- outbox service — enqueues idempotent domain work;
- device service — registration, rotation, reassignment, unregister, invalidation;
- outbox processor — materializes supported outbox events into notifications/deliveries;
- delivery processor — claims and sends per-device FCM jobs;
- worker entry point — polling/orchestration only.

Request/commitment services call these abstractions and do not know worker internals.

## 14. Configuration and observability

Add settings for:

- worker poll interval;
- batch size;
- claim lease duration;
- maximum attempts;
- retry schedule/backoff;
- optional worker identifier override.

Worker logs expose at least:

- outbox claimed/completed/retried/dead;
- delivery claimed/delivered/retried/dead/skipped;
- invalid device tokens disabled;
- stale locks reclaimed;
- batch duration/failure summaries.

Never log raw FCM tokens, auth secrets, patient-sensitive payloads, phone numbers, or exact coordinates.

P3.1 does not require a metrics backend.

## 15. Migration behavior

Migration is data-preserving:

- keep all `Notification` rows and backfill unique `event_id` values;
- keep all existing FCM rows and assign deterministic legacy `device_id` values;
- do not fabricate historical delivery rows because historical provider state is unknown;
- new delivery tracking begins after migration;
- do not modify existing blood-request, commitment, or donation-history data;
- startup remains schema-read-only and still fails unless Alembic is at head.

Downgrade may explicitly refuse if removing outbox/delivery/device-history state cannot be represented losslessly.

## 16. Test strategy

### Migration/model tests

- `0002_multi_donor -> 0003_notification_outbox` upgrade;
- legacy FCM rows preserved;
- unique legacy device IDs generated;
- notification `event_id` backfill unique/non-null;
- `(user_id, device_id)` uniqueness;
- non-null token uniqueness;
- `dedupe_key` uniqueness;
- `(notification_id, fcm_token_id)` uniqueness;
- required FKs/indexes/status constraints.

### Device API tests

- `device_id` required;
- two devices coexist for one user;
- same-device token rotation updates only that installation;
- token reassignment to another authenticated account disables/tombstones the old installation first;
- old unresolved deliveries cannot follow a reassigned token;
- unregister is caller-scoped and privacy-safe;
- unregister skips unresolved jobs but preserves delivered audit history;
- inactive installation can be safely reactivated.

### Transaction tests

- business rollback removes notification and delivery rows;
- successful lifecycle action creates one inbox notification and jobs for all active devices;
- zero-device user still receives inbox notification;
- inactive devices receive no new jobs.

### Fan-out/idempotency tests

- request creation writes outbox event in same transaction;
- no FastAPI `BackgroundTasks` donor-fan-out dependency remains;
- repeated/reclaimed `blood_request_created` processing cannot duplicate inbox notifications;
- repeated processing cannot duplicate per-device delivery rows;
- event completion and notification materialization commit atomically;
- stale/cancelled/full request no-ops appropriately;
- P2 compatibility/eligibility/availability/radius/cap behavior remains unchanged.

### Delivery worker tests

- provider success -> `Delivered` with provider message ID;
- transient failure -> `Retry` with future `available_at`;
- fifth failed attempt -> `Dead`;
- invalid token -> installation disabled and unresolved jobs skipped;
- malformed payload -> bounded dead-letter behavior;
- Firebase unavailable does not affect business API success;
- ownership mismatch causes `Skipped` without an FCM call;
- stale lock is reclaimable.

### PostgreSQL concurrency tests

- two workers racing on the same due batch do not claim the same row simultaneously;
- outbox fan-out remains idempotent under crash/reclaim/retry;
- delivery rows remain single-claim per lease;
- migration and worker queries run on PostgreSQL 16 CI.

### Regression tests

Preserve current behavior for:

- notification ownership isolation;
- read/unread filtering;
- deep-link payloads;
- accept/confirm/cancel notifications;
- business success despite push-provider failure;
- full existing SQLite test suite;
- PostgreSQL migration/integration suite.

## 17. Deployment sequence

1. Deploy code and run `alembic upgrade head` to `0003_notification_outbox`.
2. Start/update FastAPI instances.
3. Start at least one dedicated notification worker.
4. Confirm worker can claim due rows and Firebase configuration is available where push delivery is expected.
5. Scale worker replicas only on PostgreSQL; SQLite remains single-worker local/test only.

The API remains functional if the worker is temporarily down; outbox and delivery rows accumulate durably and resume when a worker returns.

## 18. Acceptance criteria

P3.1 is complete only when all of the following are demonstrated by tests/CI:

1. Creating a blood request cannot lose donor fan-out because of an API crash after commit.
2. Direct lifecycle notification creation is atomic with its business transaction.
3. FCM delivery jobs survive process restart and transient failure.
4. One user can register multiple installations independently.
5. A reassigned FCM token can never deliver an old user's pending notification to the new owner.
6. Invalid tokens are retired without failing business actions.
7. Retries use bounded backoff and end in a visible terminal state.
8. Replayed outbox work cannot duplicate inbox notifications or device delivery rows.
9. Multiple PostgreSQL workers can claim work safely.
10. Existing notification privacy/deep-link behavior and P2 request rules remain green.
11. `main` remains schema-mutation-free at application startup; Alembic remains the schema owner.
