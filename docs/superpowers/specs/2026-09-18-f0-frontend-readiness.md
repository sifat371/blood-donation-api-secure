# F0 Frontend Readiness Design

## Goal

Freeze a coherent backend contract that an Expo/React Native frontend can depend on without showing impossible donor actions, losing donor commitments, or bypassing P3.1 durability through AI chat.

## Scope

F0 makes seven focused changes:

1. Donor discovery treats the `blood_group` input as the recipient/requested group and returns all eligible red-cell-compatible donors, not exact-group-only donors.
2. Nearby request discovery returns only requests the authenticated donor can actually accept: complete donor profile, currently available, donation-history eligible, blood compatible, open capacity, not own request, and not already completed by that donor.
3. Add a paginated donor-facing active commitments endpoint: `GET /api/v1/blood-requests/commitments/mine`. Each item contains the donor commitment plus the privacy-authorized `BloodRequestResponse` for that request.
4. Add recipient-only `POST /api/v1/blood-requests/{request_id}/commitments/{commitment_id}/release`. Only a `Committed` unit may be released; completed units cannot be released. Releasing clears its slot, marks the commitment `Cancelled`, recalculates request status/capacity, audits the action, and notifies the donor.
5. New blood requests require both latitude and longitude. REST and MCP/chat use the same validation contract; creating a locationless request must return validation failure rather than a request invisible to location-based discovery.
6. REST and MCP/chat request creation share one service transaction that writes `BloodRequest + audit + P3.1 blood_request_created outbox event` atomically. The chat layer must not call the legacy synchronous/best-effort fan-out path.
7. Notification API responses expose `data` as a parsed JSON object (or null) rather than a JSON-encoded string.

## Shared discovery boundary

Create `app/services/discovery_service.py` so REST and MCP do not duplicate donor/request matching rules.

- `find_compatible_donors(...)` applies blood compatibility, availability, eligibility, location/radius, optional administrative filters, self-exclusion, distance ordering, and pagination inputs supplied by the caller.
- `find_acceptable_nearby_requests(...)` first expires stale requests, then applies donor readiness (`blood_group`, `phone`, availability, 90-day rule), RBC compatibility, open status/capacity, self-exclusion, completed-commitment exclusion, radius, and distance ordering.

P3.1 notification fan-out may continue to own its capped notification-selection query, but it must use the same compatibility/eligibility primitives. Acceptance remains the authoritative final validation under concurrency.

## Request creation boundary

Add a service-level request creation function used by both REST and MCP. It accepts an already validated `BloodRequestCreate`, adds the request, flushes for the id, writes the audit entry and `blood_request_created` outbox event in the same transaction, commits, refreshes, and returns the request. Any exception rolls the whole transaction back.

## Active commitments response

Add a schema such as `DonorCommitmentResponse` containing:

- `commitment: CommitmentResponse`
- `request: BloodRequestResponse`

Only the authenticated donor's commitment rows are returned. F0 treats `Committed` as active; completed/withdrawn/cancelled rows remain available through donation history or request-specific views rather than the active list.

## Release semantics

Recipient release/no-show is distinct from donor withdrawal and whole-request cancellation. The request recipient may release only an existing `Committed` commitment belonging to that request. The operation is transactionally idempotent only for the already-cancelled result if needed by retries; a `Completed` commitment returns conflict and is never undone. A new notification type `Commitment Released` may be used without a schema migration because notification type is stored as text.

## Notification response contract

`Notification.data` remains text in persistence for migration compatibility. `NotificationResponse` transforms it at the API boundary to `dict | None`. Invalid historical JSON is returned as `null` rather than causing a 500. Worker payload generation continues to use persisted JSON and is not changed by this response-only cleanup.

## Tests and compatibility

- TDD for every behavior change.
- Existing privacy rules remain unchanged.
- Existing route names remain unchanged except for the two new routes.
- No Alembic migration is required for F0.
- Run the complete SQLite suite and the PostgreSQL CI subset before merge.
- Add F0 API/service tests to the PostgreSQL job where they exercise concurrency-sensitive request/commitment behavior.

## Out of scope

P3.2 refresh-token concurrency hardening, P3.3 AI confirmation/idempotency/rate limiting, password reset/change, Firebase `fid` migration, frontend implementation, and production hosting manifests remain separate work.