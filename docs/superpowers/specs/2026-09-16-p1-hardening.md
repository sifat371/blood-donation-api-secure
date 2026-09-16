# P1 Hardening Specification

## Scope

P1 strengthens reliability, validation, time semantics, and database portability without changing the single-donor request model.

1. Blood-request acceptance must be race-safe: only one pending-to-accepted transition may succeed.
2. AI/MCP tool arguments are untrusted and must be validated with the same bounds as REST inputs; unknown arguments are rejected after identity arguments are stripped; donor/request search results are capped.
3. New blood requests may be needed today or later, never in the past. Existing pending requests whose needed date is past become `Expired`, are hidden from donor discovery, cannot be accepted, and do not trigger donor fan-out.
4. Business dates use the configured Bangladesh business timezone (`Asia/Dhaka` by default). Stored datetimes remain UTC-naive for compatibility, but are generated through one helper using timezone-aware UTC internally.
5. Blood-request lifecycle writes (state, audit row, in-app notification) commit atomically. Push delivery happens only after the database commit and remains best-effort.
6. Refresh-token rotation revokes the old token and persists the replacement in one transaction, and does not extend sessions for missing/unverified users.
7. Database engine options must be dialect-aware. Legacy startup migrations are explicitly SQLite-only so PostgreSQL URLs do not execute SQLite PRAGMAs. Document the Alembic handoff required before production PostgreSQL.
8. Preserve all P0 privacy/authentication guarantees and existing API response shapes except the new `Expired` request status and validation failures for invalid/past inputs.
