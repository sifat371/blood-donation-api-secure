# P1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make request claiming race-safe, validate AI tool inputs, expire stale requests, centralize time semantics, reduce fragmented transactions, and make database startup dialect-aware.

**Architecture:** Add focused clock and MCP-schema modules, keep lifecycle policy in `request_service`, and use compare-and-set SQL for acceptance. Database rows for lifecycle/audit/notification are staged in one SQLAlchemy transaction; FCM remains post-commit best-effort. Legacy migrations stay available for SQLite while non-SQLite deployments are directed to an Alembic migration path.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy, Pydantic v2, SQLite, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-16-p1-hardening.md`

## Global Constraints

- Preserve the existing single-donor `accepted_by` data model.
- Preserve P0 privacy and authentication behavior.
- Keep SQLite development/test support.
- Do not require a new runtime dependency in P1; PostgreSQL migration tooling is documented as the next deployment step rather than silently pretending manual SQLite migrations are portable.
- CI must pass the complete pytest suite before merge.

---

### Task 1: Central clock and request-date semantics

**Files:**
- Create: `app/core/time.py`
- Modify: `app/core/config.py`
- Modify: `app/db/models.py`
- Modify: `app/schemas/blood_request.py`
- Modify: timestamp callers under `app/api`, `app/services`, and `app/mcp_tools`
- Test: `tests/test_api_blood_requests.py`, `tests/test_e2e_multi_user.py`, `tests/test_time.py`

**Interfaces:**
- Produces: `utc_now() -> datetime`, `business_today() -> date`
- Consumes: `settings.business_timezone`

- [ ] Write tests that today is accepted, yesterday is rejected, and donation completion uses `business_today()`.
- [ ] Add the clock helpers and timezone setting.
- [ ] Replace application `datetime.utcnow()`/local `datetime.now()` writes with the shared clock.
- [ ] Run focused tests, then compile all Python sources.

### Task 2: Stale-request expiry

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/services/request_service.py`
- Modify: `app/api/v1/blood_requests.py`
- Modify: `app/mcp_tools/tools.py`
- Test: `tests/test_api_blood_requests.py`, `tests/test_mcp_tools.py`

**Interfaces:**
- Produces: `RequestStatus.EXPIRED`, `expire_stale_requests(session)`, `expire_request_if_stale(session, request)`

- [ ] Write tests proving stale pending requests become Expired, disappear from nearby discovery, cannot be accepted, and do not fan out notifications.
- [ ] Implement lazy expiry using the business date.
- [ ] Run focused tests and compile.

### Task 3: Atomic request acceptance and lifecycle transactions

**Files:**
- Modify: `app/services/request_service.py`
- Modify: `app/services/auth_service.py`
- Modify: `app/services/notifications.py`
- Modify: `app/api/v1/blood_requests.py`
- Modify: `app/mcp_tools/tools.py`
- Test: `tests/test_blood_requests.py`, `tests/test_api_blood_requests.py`, `tests/test_api_notifications.py`

**Interfaces:**
- Produces: `_claim_pending_request(...) -> bool`, `audit_log(..., commit: bool = True)`, `create_notification(..., commit: bool = True)`, `send_notification_push(...)`

- [ ] Write a two-session compare-and-set regression test and tests for atomic DB-side audit/notification rows.
- [ ] Implement conditional `UPDATE ... WHERE status='Pending'` claim semantics.
- [ ] Stage lifecycle state, audit row, and notification row before one commit; deliver push afterward.
- [ ] Make request creation and MCP creation commit request + audit together.
- [ ] Run focused tests and compile.

### Task 4: MCP argument validation and result caps

**Files:**
- Create: `app/schemas/mcp.py`
- Modify: `app/mcp_tools/tools.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces: `validate_tool_args(tool_name, args) -> dict`, `MCP_MAX_RESULTS = 20`

- [ ] Write tests for invalid blood groups, impossible coordinates, excessive radii, non-boolean availability, extra arguments, and result caps.
- [ ] Add strict Pydantic argument models matching REST bounds.
- [ ] Validate after identity stripping and before dispatch.
- [ ] Cap sorted donor/request results to 20.
- [ ] Run focused tests and compile.

### Task 5: Refresh rotation transaction integrity

**Files:**
- Modify: `app/services/auth_service.py`
- Modify: `app/api/v1/auth.py`
- Test: `tests/test_api_auth.py`, `tests/test_auth.py`

**Interfaces:**
- Produces: transactional refresh rotation that refuses missing/unverified users before replacement issuance.

- [ ] Add regression tests for unverified/deleted users and single-transaction replacement behavior.
- [ ] Refactor refresh-token creation to support caller-owned transactions.
- [ ] Rotate old/new refresh rows in one commit and reject invalid account state before issuing replacements.
- [ ] Run focused tests and compile.

### Task 6: Database portability boundary and migration documentation

**Files:**
- Modify: `app/db/database.py`
- Modify: `app/db/migrations.py`
- Create: `docs/database-migrations.md`
- Test: `tests/test_database_config.py`

**Interfaces:**
- Produces: `_engine_kwargs(database_url) -> dict`; legacy migration runner that no-ops outside SQLite.

- [ ] Write tests proving SQLite gets `check_same_thread=False` and non-SQLite URLs do not.
- [ ] Make engine options dialect-aware.
- [ ] Guard SQLite PRAGMA migrations by dialect.
- [ ] Document the Alembic handoff for PostgreSQL and production schema changes.
- [ ] Run focused tests and compile.

### Task 7: Full verification and branch completion

**Files:**
- Modify tests only if a test is demonstrably stale relative to the P1 spec; do not weaken production behavior to satisfy old assertions.

- [ ] Run `python -m compileall -q app tests scripts` locally.
- [ ] Push the P1 branch and open a PR so GitHub Actions runs `uv sync --frozen --dev` and full `pytest -q`.
- [ ] Diagnose any CI failure from logs/artifacts, fix on the P1 branch, and rerun.
- [ ] Merge/fast-forward only when full CI is green.
