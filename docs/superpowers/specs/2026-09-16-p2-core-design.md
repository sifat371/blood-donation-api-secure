# P2 Core Design: Versioned Migrations and Multi-Donor Blood Requests

## Status

Approved in design review on 2026-09-16. This specification is the implementation contract for P2. Implementation must not weaken the P0 privacy/authentication guarantees or the P1 reliability guarantees.

## Goals

P2 has two ordered goals:

1. Replace ad-hoc/startup schema mutation with versioned Alembic migrations while preserving existing SQLite data and preparing the same schema for PostgreSQL.
2. Replace the single `blood_requests.accepted_by` model with a multi-donor commitment model where one donor represents one unit, committed and completed units are tracked separately, and concurrency cannot overbook a request.

P2 intentionally preserves the current single-recipient blood-request concept. Durable queue infrastructure, moderation, and additional AI confirmation UX remain later work.

## Non-goals

- No destructive reset of existing installations.
- No fabricated historical donors or units during migration.
- No multi-unit commitment by one donor to the same request.
- No durable notification worker/outbox in this phase.
- No full frontend redesign in this phase.
- No downgrade that pretends real multi-donor P2 data can be losslessly collapsed back into one `accepted_by` value.

## 1. Architecture

P2 uses a compatibility-first migration. `BloodRequest` remains the parent aggregate. Donor participation moves to a new `DonationCommitment` table. REST and MCP/chat entry points delegate to one commitment/request service layer so authorization, transitions, counts, audit rows, donation history, and notifications cannot drift.

Existing API routes remain where practical. The current accept route changes implementation semantics: accepting creates or reactivates one donor commitment rather than writing `blood_requests.accepted_by`.

The legacy `accepted_by` database column is removed by the P2 migration after its data has been transformed. Deprecated response fields may be derived temporarily for compatibility, but they are never a second source of truth.

## 2. Data Model

### 2.1 BloodRequest

`BloodRequest.units` remains the required number of units. The API also exposes it as `units_required` for clarity.

Request status values become:

- `Pending`
- `Partially Committed`
- `Fully Committed`
- `Completed`
- `Cancelled`
- `Expired`

`Accepted` is a legacy-only input status handled by the migration and is not produced by new P2 application behavior.

Add:

- `legacy_completion_incomplete: bool = False`

This flag is only for migrated legacy requests that were historically marked `Completed` with `units > 1` even though the old schema could identify at most one donor. It prevents P2 from inventing missing donors.

### 2.2 DonationCommitment

Create `donation_commitments` with at least:

- `id` primary key
- `request_id` foreign key to `blood_requests.id`, indexed
- `donor_id` foreign key to `users.id`, indexed
- `status`: `Committed | Completed | Withdrawn | Cancelled`
- `committed_at`
- `completed_at`, nullable
- `withdrawn_at`, nullable
- `cancelled_at`, nullable
- `created_at`
- `updated_at`

A database uniqueness constraint on `(request_id, donor_id)` guarantees one commitment record per donor per request. If a donor withdraws and later recommits while a slot is available, the same row is reactivated to `Committed`; audit logs preserve the transition history. This avoids dialect-specific partial unique-index semantics while still enforcing one active commitment per donor.

A donor contributes exactly one unit to a request.

### 2.3 DonationHistory

Donation history remains one row per completed donation. Add nullable `commitment_id` referencing `donation_commitments.id`, unique when non-null. Confirming one commitment creates exactly one donation-history row and links it to that commitment. This provides idempotency and traceability.

Legacy donation-history rows are preserved. The migration backfills `commitment_id` only when the matching request/donor relationship is unambiguous; otherwise it remains null.

## 3. Derived Counts and Request Status

For non-legacy active requests:

- `units_required = request.units`
- `units_committed = count(commitments where status == Committed)`
- `units_completed = count(commitments where status == Completed)`
- `units_secured = units_committed + units_completed`
- `remaining_units = max(units_required - units_secured, 0)`

Status is recalculated server-side after every commitment transition:

- `Completed` when `units_completed == units_required`
- `Fully Committed` when `units_secured == units_required` and completion is not yet complete
- `Partially Committed` when `0 < units_secured < units_required`
- `Pending` when `units_secured == 0`

`Cancelled` and `Expired` are terminal overrides and are not recalculated into an active state.

For `legacy_completion_incomplete=True`, the historical request remains `Completed`. `units_completed` reports only the known migrated donor count, and `remaining_units` is `null` because the missing historical unit provenance is unknown. The API exposes the legacy flag so clients do not interpret the known count as a contradiction.

## 4. Lifecycle Rules

### 4.1 Accept / Commit

`POST /blood-requests/{id}/accept` remains available.

The authenticated donor may commit only when:

- the request is not their own;
- the request is not terminal;
- the needed date has not expired under the active-request expiry rule;
- their donor profile is complete;
- they are available;
- they satisfy the recorded donation cooldown rule;
- their blood group is compatible;
- fewer than `units_required` units are already secured.

Acceptance creates a new commitment or reactivates that donor's existing `Withdrawn`/`Cancelled` commitment when allowed. Duplicate active acceptance is idempotent/conflict-safe and never creates another row. When no slot remains, acceptance returns HTTP 409.

### 4.2 Withdraw

Add `POST /blood-requests/{id}/withdraw`.

Only the donor may withdraw their own `Committed` commitment. `Completed` commitments are immutable. Withdrawal sets the commitment to `Withdrawn`, records `withdrawn_at`, and recalculates the request status. A fully committed request may therefore return to partially committed or pending.

### 4.3 Confirm a Donation

Add `POST /blood-requests/{id}/commitments/{commitment_id}/confirm`.

Only the request recipient may confirm. The commitment must belong to that request and be `Committed`. Confirmation atomically:

1. marks the commitment `Completed`;
2. records `completed_at`;
3. creates one linked `DonationHistory` row;
4. updates the donor's `last_donation_date` to the business date;
5. recalculates request status;
6. writes audit and in-app notification rows;
7. commits once;
8. sends best-effort push only after commit.

Repeat confirmation is idempotent and must not create duplicate history.

### 4.4 Complete Compatibility Route

`POST /blood-requests/{id}/complete` remains for compatibility but no longer creates donation history or completes donor commitments. It succeeds only when all required commitments are already confirmed completed. Otherwise it returns a validation/conflict error explaining that outstanding donations must be confirmed individually.

### 4.5 Cancel

The recipient may cancel an active request. In the same database transaction:

- request status becomes `Cancelled`;
- every `Committed` commitment becomes `Cancelled`;
- `Completed` commitments remain completed and their donation history remains intact;
- affected donors receive in-app notifications, with push sent after commit.

### 4.6 Expiry

Automatic expiry applies only to requests with no secured units: a request whose `needed_date < business_today()` and whose derived secured count is zero becomes `Expired`.

Requests with committed or completed units do not auto-expire; the recipient must complete or cancel them. This avoids silently invalidating an active real-world donor arrangement.

Expired requests do not appear in donor discovery, cannot accept commitments, and do not trigger donor fan-out.

## 5. Concurrency and Transaction Safety

The service layer owns transitions.

Required invariants:

- one commitment row per donor/request;
- one donor contributes one unit;
- secured units never exceed requested units;
- confirmation creates at most one donation-history row;
- lifecycle state + commitment + history + audit + in-app notification changes commit atomically;
- external push happens only after successful database commit.

PostgreSQL acceptance locks the parent request row while checking/allocating a slot. SQLite uses a write transaction plus the same invariant checks and database uniqueness constraint. The service treats a lost allocation race as HTTP 409 rather than silently overbooking.

Tests must exercise concurrent acceptance, including more simultaneous donors than available slots.

## 6. API Contract and Privacy

Existing request response fields remain where possible and add:

- `units_required`
- `units_committed`
- `units_completed`
- `remaining_units: int | null`
- `legacy_completion_incomplete`
- `my_commitment_status`, nullable

Keep `units` during the compatibility period as the same value as `units_required`.

Deprecated compatibility fields `accepted_by`, `donor_name`, and `donor_phone` are derived only when exactly one commitment is visible to that viewer. They are never persisted as request truth. If multiple donors exist, these deprecated singular fields are null.

Add `GET /blood-requests/{id}/commitments`:

- recipient: sees active/completed commitment identities, statuses, timestamps, donor name and donor phone needed for handoff;
- a donor: sees only their own commitment for that request;
- unrelated authenticated users: receive no donor commitment details.

P0 privacy remains unchanged: unrelated users never receive donor phone numbers, patient contact information, or exact private request location fields.

## 7. MCP / AI Integration

MCP tools continue using the same service layer and strict P1 argument validation.

- existing `UpdateRequest: accept` creates/reactivates a one-unit commitment;
- cancellation uses the shared request service;
- compatibility `complete` obeys the new all-confirmed rule;
- add commitment operations for donor withdrawal and recipient confirmation with validated positive IDs and server-side identity enforcement.

No model-supplied `user_id`, `donor_id`, `recipient_id`, or ownership field is trusted. P2 does not broaden what PII is sent to the language model.

Explicit human confirmation UX for AI mutations remains a later hardening phase; P2 only ensures the underlying mutations are authorized and consistent.

## 8. Alembic and Database Migration Strategy

Add Alembic and PostgreSQL driver support (`psycopg`) to the locked project dependencies.

### Revision 1: P1 Baseline

The baseline revision represents the complete schema on `main` immediately before P2. On an empty database, running Alembic from base creates that schema.

An existing database has no `alembic_version` table. It must not be blindly stamped. A migration helper verifies the expected P1 tables and critical columns before stamping the baseline. Unknown or incomplete schemas are rejected with a clear error.

### Revision 2: Multi-Donor Model

The second revision:

1. creates `donation_commitments` and required indexes/constraints;
2. adds `legacy_completion_incomplete` to `blood_requests`;
3. adds nullable `donation_history.commitment_id` and its constraint/index;
4. transforms legacy request/donor state into commitment rows;
5. transforms request status values to the P2 lifecycle;
6. backfills history links where unambiguous;
7. removes `blood_requests.accepted_by` after successful transformation.

### Existing-row transformation

- Legacy `Pending` -> `Pending`, no commitment.
- Legacy `Accepted` with `accepted_by` -> one `Committed` commitment. Status becomes `Fully Committed` if `units == 1`, otherwise `Partially Committed`.
- Legacy `Completed` with `accepted_by` -> one `Completed` commitment. If `units == 1`, normal `Completed`. If `units > 1`, remain `Completed` and set `legacy_completion_incomplete=True`.
- Legacy `Cancelled`/`Expired` -> preserve terminal request status. If a historical `accepted_by` exists, preserve that donor relationship as a terminal `Cancelled` commitment rather than discarding it.
- No missing donors or units are invented.

### Existing SQLite helper

Provide `scripts/migrate_existing_db.py` that:

1. validates the configured database is SQLite and points to an existing file;
2. verifies the P1 legacy schema;
3. creates a timestamped backup beside the database;
4. stamps the P1 baseline revision;
5. runs `alembic upgrade head`;
6. verifies the resulting revision and critical row-count/data invariants;
7. exits non-zero without deleting the backup if any step fails.

The helper must be safe to rerun: already-versioned databases do not get stamped again and simply upgrade from their current Alembic revision.

### Application startup

Application startup no longer performs schema-changing migrations. Deployment performs:

```bash
uv run alembic upgrade head
```

Tests may still use `SQLModel.metadata.create_all()` for isolated ephemeral databases, but production/staging schema evolution is Alembic-owned.

Production startup should fail clearly when its database schema is behind the expected Alembic head rather than mutating it automatically.

## 9. PostgreSQL Target

SQLite remains supported for development/test and existing installations. PostgreSQL becomes the intended production database.

- engine options remain dialect-aware;
- production configuration accepts a PostgreSQL `DATABASE_URL`;
- no SQLite PRAGMA/manual migration path runs against PostgreSQL;
- Alembic migrations define the canonical target schema for both dialects.

CI adds a PostgreSQL service job (PostgreSQL 16 or newer) that upgrades a fresh database to head and runs database-sensitive service/API tests against it.

## 10. Testing Strategy

### Migration tests

Create a real P1 SQLite fixture database containing representative rows for:

- pending request;
- one-unit accepted request;
- multi-unit accepted request;
- one-unit completed request;
- multi-unit completed legacy request;
- cancelled/expired request with historical donor link where applicable;
- existing donation history, notifications, audit logs, auth rows and users.

Tests run the actual Alembic/helper path and assert:

- all original user/request/history/audit/notification records remain present;
- expected commitments are created with correct statuses;
- legacy incomplete completion is flagged rather than fabricated;
- accepted-by data has been preserved before the old column disappears;
- current Alembic revision is head;
- rerunning the helper is safe.

Fresh-database tests run `alembic upgrade head` from an empty SQLite database and from an empty PostgreSQL database.

Downgrade testing is only required for revisions/steps that are explicitly lossless. P2 will not claim a lossless downgrade from real multi-donor production data to the one-donor P1 schema.

### Service/API tests

Add tests for:

- 1-unit and N-unit acceptance;
- partial and full commitment statuses;
- completed counts separate from committed counts;
- withdrawal and recommit;
- authorization for withdraw/confirm/list commitments;
- completed commitment immutability;
- recipient confirmation and donation-history idempotency;
- cooldown updated only at confirmation;
- cancellation with mixed committed/completed donors;
- expiry only when zero units are secured;
- overbooking prevention under concurrent acceptance;
- public/privacy response behavior;
- deprecated singular-field behavior with zero/one/multiple commitments;
- MCP parity and strict argument validation.

### Regression gate

P2 cannot merge until:

- migration of the representative legacy fixture succeeds;
- fresh SQLite upgrade reaches head;
- fresh PostgreSQL upgrade reaches head;
- all existing P0/P1 tests remain green or are deliberately updated for the new documented contract;
- all new P2 tests pass;
- PR CI passes on GitHub's merge result;
- final `main` CI passes after merge.

## 11. Rollout

Implementation occurs on `p2-core`.

Before deploying an existing SQLite installation:

1. stop application writes;
2. run the migration helper, retaining its generated backup;
3. inspect migration output/revision;
4. restart the P2 application;
5. perform smoke tests for login, request listing, commitment acceptance, withdrawal, and confirmation.

For new production PostgreSQL environments, create the database, configure `DATABASE_URL`, run `uv run alembic upgrade head`, then start the API.

## 12. Success Criteria

P2 is complete only when:

- existing P1 SQLite data upgrades without destructive reset;
- multi-unit requests support multiple distinct one-unit donors;
- committed and completed units are independently observable;
- concurrent acceptance cannot exceed the requested unit count;
- donor withdrawal and recipient confirmation are authorized and transactional;
- donation history is created once per confirmed commitment;
- P0 privacy and P1 transaction/auth guarantees remain intact;
- SQLite and PostgreSQL share the same Alembic-defined target schema;
- the permanent `main` CI is green after merge.
