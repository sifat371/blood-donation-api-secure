# P3.1 Durable Notifications & Device Delivery — Design

Date: 2026-09-17
Branch: `p3.1-durable-notifications`
Base: `main` at `2439d00d1cec1683493fde4cfe7fe5ded53236fb`

## 1. Purpose

P3.1 replaces best-effort push notification delivery with a durable notification pipeline while preserving the existing in-app notification inbox and P2 request lifecycle semantics.

The phase has four goals:

1. Ensure notification-generating business events survive API-process crashes.
2. Ensure FCM delivery work survives worker/API restarts and transient provider failures.
3. Support multiple active devices per user without one registration silently replacing another.
4. Make delivery observable, retryable, idempotent, and safe under multiple PostgreSQL worker instances.

P3.1 adds no Redis, Celery, Kafka, or external queue. PostgreSQL is the production coordination mechanism. SQLite remains supported for local development/tests with a single notification worker only.

## 2. Current-state problems

The current implementation already persists `Notification` rows, but FCM sending is best-effort after the surrounding business transaction commits. A crash between commit and the FCM send permanently loses the push attempt even though the in-app notification exists.

Blood-request creation has a second durability gap: it commits the request and then schedules donor fan-out using FastAPI `BackgroundTasks`. If the process dies after the request commit but before or during that background task, the request survives but donor notifications may never be generated.

FCM registration also effectively behaves as one token per user because `/profile/fcm-token` updates the first row found for that user. That does not model multiple installations and does not safely handle token rotation.

## 3. Scope

### In scope

- Transactional database outbox for recoverable domain notification work.
- Durable per-device push-delivery queue.
- Multi-device FCM installation model.
- Stable client-provided `device_id` per installation.
- Device registration, token rotation, re-ownership, and unregister.
- Per-device delivery status, attempts, provider result, and failure classification.
- Exponential retry with jitter and dead-letter state.
- Permanent invalid-token retirement.
- Stable notification `event_id` in API and push payloads.
- Durable donor fan-out for newly created blood requests.
- Dedicated Python notification worker process.
- PostgreSQL-safe concurrent claiming with `FOR UPDATE SKIP LOCKED`.
- Stale-lock reclamation after worker crashes.
- SQLite single-worker fallback for local/test use.
- Data-preserving Alembic migration.
- Regression coverage for existing notification privacy, lifecycle, and deep-link behavior.

### Out of scope

- Redis/Celery/Kafka.
- WebSockets or realtime inbox streaming.
- Email/SMS notification channels.
- Push preference controls by notification type.
- Auth-session/device-session coupling; that belongs to P3.2.
- Chat/AI rate limiting or action confirmation; that belongs to P3.3.
- Trust/moderation systems; that belongs to P3.4.
- Retrofactive push attempts for historical notifications.

## 4. Approved architecture

P3.1 uses two durable queue-like structures with separate responsibilities:

1. `outbox_events`: durable domain work where notification recipients are not yet materialized, initially blood-request donor fan-out.
2. `notification_deliveries`: one durable push-delivery job per notification and device installation.

`Notification` remains the user-facing in-app inbox and source of truth for notification content. Queue rows are operational state, not business truth.

The API never waits for FCM and never makes business success depend on provider availability.

## 5. Data model

Alembic revision: `0003_notification_outbox`.

### 5.1 `notifications`

Keep the existing numeric primary key and fields. Add:

- `event_id`: stable UUID-style string, non-null and unique.

Rules:

- Every new notification receives an `event_id` at creation time.
- Existing rows are backfilled during migration with unique generated values.
- `event_id` is returned by the notification API and included in every FCM payload.
- Client deduplication uses `event_id`, while numeric `id` remains the server-side/API identifier.

### 5.2 `fcm_tokens`

Evolve the table from token storage into installation records.

Fields:

- existing: `id`, `user_id`, `token`, `device_info`, `created_at`
- add: `device_id` (non-null)
- add: `is_active` (bool, default true)
- add: `last_seen_at`
- add: `updated_at`
- add: `disabled_at` nullable
- add: `last_failure_reason` nullable

Constraints:

- unique `(user_id, device_id)`
- unique `token`
- indexed `user_id`
- indexed active-state lookup as appropriate for supported dialects

Migration of existing rows:

- Preserve every existing token row.
- Assign a deterministic legacy `device_id` derived from the row identity, for example `legacy-<id>`.
- Mark migrated rows active.
- Do not merge or delete legacy token rows during migration.

### 5.3 `outbox_events`

Purpose: recoverable domain notification work.

Fields:

- `id`
- `event_type`
- `aggregate_type`
- `aggregate_id`
- `payload_json`
- `idempotency_key` unique
- `status`: `Pending`, `Processing`, `Completed`, `Dead`
- `attempts`
- `available_at`
- `locked_at` nullable
- `locked_by` nullable
- `last_error` nullable
- `created_at`
- `updated_at`
- `completed_at` nullable

Initial event type:

- `blood_request_created`

Idempotency example:

- one stable key per request-creation fan-out, such as `blood_request_created:<request_id>`.

### 5.4 `notification_deliveries`

Purpose: durable push job plus delivery audit for one notification/device pair.

Fields:

- `id`
- `notification_id` FK
- `fcm_token_id` FK
- `status`: `Pending`, `Processing`, `Delivered`, `Retry`, `Dead`, `Skipped`
- `attempts`
- `available_at`
- `locked_at` nullable
- `locked_by` nullable
- `provider_message_id` nullable
- `last_error` nullable
- `last_error_category` nullable
- `delivered_at` nullable
- `created_at`
- `updated_at`

Constraint:

- unique `(notification_id, fcm_token_id)`

The FCM token string is read from the installation row at send time. Registration/token rotation therefore updates the installation identity without creating duplicate installation rows.

## 6. Delivery semantics

P3.1 provides at-least-once delivery semantics, not exactly-once delivery.

True exactly-once FCM delivery cannot be guaranteed because a provider timeout can happen after FCM accepts a message but before the worker receives the acknowledgement.

Mitigations:

- stable `event_id` and `notification_id` in push payloads;
- unique notification/device delivery rows;
- delivered device rows are never intentionally resent;
- retries target unresolved delivery rows only;
- clients can suppress duplicate presentation using `event_id`.

## 7. Business write paths

### 7.1 Direct lifecycle notifications

Examples:

- donor commits to request -> notify recipient;
- recipient confirms donation -> notify donor;
- request cancellation -> notify affected committed donors.

The existing business transaction must atomically persist:

- the business state change;
- audit rows;
- the in-app `Notification` row;
- one `NotificationDelivery` row for every currently active installation belonging to the recipient.

If the transaction rolls back, none of those notification/delivery rows remain.

No network call occurs before or during commit.

If the user has no active device, the in-app notification is still created. Registering a device later does not create delivery rows for old notifications.

### 7.2 Blood-request donor fan-out

Request creation must atomically persist:

- `BloodRequest`;
- its audit row;
- `OutboxEvent(event_type='blood_request_created')`.

The request endpoint no longer depends on FastAPI `BackgroundTasks` for donor fan-out.

The worker later processes the event by:

1. loading the request;
2. re-checking that it is still an open-capacity request;
3. applying the same P2 compatibility, availability, eligibility, location-radius, and maximum-recipient rules;
4. selecting recipients deterministically;
5. creating each recipient's `Notification` and current active-device `NotificationDelivery` rows transactionally;
6. completing the outbox event only when materialization commits successfully.

Reprocessing must be harmless. The event idempotency key plus deterministic notification creation/idempotency rules must prevent duplicate donor inbox notifications and duplicate delivery jobs.

## 8. Worker design

Run a separate Python process, for example:

`uv run python -m app.workers.notification_worker`

The worker performs two loops/batches:

1. claim/process due `outbox_events`;
2. claim/send due `notification_deliveries`.

### PostgreSQL claiming

Use short transactions and `SELECT ... FOR UPDATE SKIP LOCKED` for due rows.

When a row is claimed:

- move status to `Processing`;
- set `locked_at`;
- set `locked_by` to a worker instance identifier;
- commit the claim quickly before doing external FCM work.

Multiple workers can therefore operate concurrently without intentionally processing the same claimed row at the same time.

### SQLite behavior

SQLite remains valid for local development and the ordinary test suite but supports one notification worker only. Horizontal worker safety is a PostgreSQL production property and receives dedicated PostgreSQL integration tests.

### Stale lock reclamation

`Processing` rows with `locked_at` older than a configured lease become reclaimable. Reclamation increments/maintains attempt accounting consistently and prevents crashed workers from stranding work indefinitely.

## 9. Retry and terminal-state policy

Default retry sequence after transient failures:

- ~1 minute
- ~5 minutes
- ~30 minutes
- ~2 hours
- ~12 hours

After the fifth failed attempt, move the delivery to `Dead`.

The schedule, poll interval, batch size, lease duration, and maximum attempts are configuration values rather than business constants.

Add bounded jitter to retry timing so provider recovery does not cause synchronized retry spikes.

### Failure classification

#### Permanent token/provider rejection

Examples include unregistered/invalid device tokens.

Action:

- mark installation inactive;
- set `disabled_at` and failure reason;
- mark current delivery terminal `Skipped`;
- mark any still-pending/retry deliveries for that installation `Skipped`.

#### Transient provider/network failure

Action:

- increment attempts;
- set status `Retry`;
- compute next `available_at` using configured backoff plus jitter.

#### Malformed/internal payload error

If retry cannot plausibly fix the error, move the row to `Dead` immediately or after a very small bounded retry count. Do not retry malformed application data for hours.

#### Firebase unavailable or misconfigured

Business/API actions remain successful. Delivery rows stay unresolved/retryable so work is not silently discarded.

## 10. Device API contract

### Register/update installation

Keep endpoint path:

`POST /api/v1/profile/fcm-token`

New request body:

```json
{
  "device_id": "stable-installation-id",
  "fcm_token": "current-fcm-token",
  "device_info": "android"
}
```

`device_id` is required in P3.1. This is an intentional API contract change.

Behavior:

- `(authenticated user, device_id)` identifies one installation;
- re-registering the same installation updates its FCM token and metadata;
- registration marks it active and refreshes `last_seen_at`/`updated_at`;
- if the provided globally unique FCM token belongs to another user/device row, ownership moves to the newly authenticated installation so the old account cannot continue receiving messages to that token;
- token rotation does not create a second row for the same installation.

### Unregister installation

Add:

`DELETE /api/v1/profile/fcm-token/{device_id}`

Behavior:

- only affects the authenticated user's matching installation;
- marks it inactive and records disable/update time;
- pending/retry delivery rows for that installation become `Skipped`;
- already delivered history remains unchanged;
- use 404 semantics for a missing/non-owned installation rather than leaking another user's device identity.

A global logout-all-devices feature is deferred to P3.2.

## 11. Notification API compatibility

Keep existing notification list/read behavior and ownership/privacy guarantees.

`NotificationResponse` gains `event_id`.

Existing fields remain, including numeric `id`, JSON-string `data`, `is_read`, and `created_at`.

Every FCM payload includes at minimum:

- `notification_id`
- `event_id`
- notification `type`
- existing deep-link payload fields such as `request_id` and `commitment_id` where present

## 12. Service boundaries

P3.1 should keep responsibilities small and testable.

Suggested boundaries:

- notification creation service: creates in-app notification and snapshots active-device delivery rows inside caller-owned transactions;
- outbox service: enqueues idempotent domain work;
- device service: registration, token rotation, unregister, invalidation;
- outbox processor: converts supported outbox events into inbox notifications/deliveries;
- delivery processor: claims and sends per-device FCM jobs;
- worker entry point: polling/loop orchestration only.

Business request/commitment services should call these abstractions rather than knowing worker internals.

## 13. Configuration

Add explicit settings for:

- worker poll interval;
- batch size;
- claim lease duration;
- maximum delivery attempts;
- retry schedule/backoff parameters;
- optional worker identifier override for diagnostics.

Production configuration continues to allow FCM to be unavailable without rejecting business requests. Missing FCM credentials means delivery work remains pending/retryable and is visible operationally rather than disappearing.

## 14. Observability

Worker logs should be structured enough to expose at least:

- outbox rows claimed/completed/retried/dead;
- deliveries claimed/delivered/retried/dead/skipped;
- invalid device tokens disabled;
- stale locks reclaimed;
- batch processing duration and failures.

Do not log raw FCM tokens, auth secrets, patient-sensitive notification payloads, phone numbers, or exact coordinates.

P3.1 does not require a metrics backend; logs and database state are sufficient for this phase.

## 15. Migration behavior

Migration must be data-preserving.

- Keep all existing `Notification` rows; backfill unique `event_id` values.
- Keep all existing FCM token rows; assign deterministic legacy `device_id` values.
- Do not fabricate historical `notification_deliveries`, because historical provider-delivery state is unknown.
- New delivery tracking starts after the migration is deployed.
- Existing request/donation/history data is untouched.
- Startup remains read-only with respect to schema and must continue to fail if Alembic is not at head.

Downgrade behavior may refuse or be intentionally limited where collapsing multi-device/installations or removing delivery/outbox history would be lossy. Any refusal must be explicit in the Alembic revision.

## 16. Testing strategy

### Migration/model tests

- upgrade from `0002_multi_donor` to `0003_notification_outbox`;
- preserve legacy FCM rows;
- backfill valid unique device IDs and notification event IDs;
- enforce `(user_id, device_id)` uniqueness;
- enforce token uniqueness;
- enforce `(notification_id, fcm_token_id)` uniqueness;
- validate expected indexes/FKs/status constraints where applicable.

### Device API tests

- `device_id` required;
- two devices can coexist for one user;
- same device token rotation updates one installation only;
- same FCM token moving to another authenticated account cannot remain active for the previous owner;
- unregister affects only caller-owned device;
- unregister skips unresolved jobs but preserves delivered audit rows;
- registration reactivates an inactive installation safely.

### Transactional notification tests

- business rollback removes notification and delivery rows;
- successful lifecycle action creates notification plus delivery rows for all active devices;
- zero-device user still receives inbox notification;
- inactive devices receive no new jobs.

### Fan-out/idempotency tests

- request creation writes an outbox event in the same transaction;
- no FastAPI `BackgroundTasks` dependency remains for donor fan-out;
- replaying `blood_request_created` cannot duplicate inbox notifications;
- replaying cannot duplicate notification/device delivery rows;
- fan-out re-checks current request state and no-ops if no longer relevant;
- fan-out continues to use P2 RBC compatibility, eligibility, availability, radius, and recipient cap rules.

### Delivery worker tests

- successful provider response -> `Delivered` with provider message ID;
- transient failure -> `Retry` with future `available_at`;
- max-attempt failure -> `Dead`;
- invalid token -> installation inactive and related unresolved jobs skipped;
- malformed payload -> dead-letter behavior;
- Firebase unavailable/misconfigured does not affect API transaction success;
- stale `Processing` jobs become reclaimable;
- already delivered jobs are not intentionally resent.

### PostgreSQL concurrency tests

Using PostgreSQL 16 CI:

- two workers claim from the same due batch concurrently;
- `SKIP LOCKED` prevents simultaneous ownership of one job;
- stale lease reclamation works after simulated crash;
- uniqueness constraints prevent duplicate fan-out/delivery materialization under concurrent/replayed processing.

### Regression tests

Preserve all current notification tests covering:

- auth required;
- owner-only visibility;
- 404 isolation for another user's notification;
- read/unread behavior;
- pagination;
- deep-link request IDs;
- accept/confirm/cancel lifecycle alerts;
- push failures never roll back completed business actions.

## 17. Rollout sequence

1. Add migration/models and schemas.
2. Add device registration/unregister service and API behavior.
3. Add transactional notification creation with delivery materialization.
4. Add outbox enqueueing and durable blood-request fan-out.
5. Add delivery/outbox processors and worker entry point.
6. Remove direct post-commit FCM send paths and request `BackgroundTasks` fan-out.
7. Add configuration/docs/operations notes.
8. Add PostgreSQL concurrency coverage and full regression run.

The API and worker must be deployable from the same code revision. Apply Alembic migration before starting either process.

## 18. Acceptance criteria

P3.1 is complete when all of the following are true:

- A committed notification-generating business action cannot lose its push job because the API process crashes after commit.
- A committed blood request cannot lose donor fan-out because the API process crashes after request creation.
- One user can maintain multiple active device installations.
- FCM token rotation updates the intended installation without silently replacing another device.
- Invalid tokens are retired without breaking business APIs.
- Transient failures retry with bounded exponential backoff and eventually dead-letter.
- Delivery state is auditable per device.
- Multiple PostgreSQL workers can safely process queue rows concurrently.
- SQLite remains usable for local/test operation with one worker.
- Existing in-app notification privacy and lifecycle behavior remains intact.
- The full SQLite suite and dedicated PostgreSQL P3.1 suite pass in GitHub Actions.
