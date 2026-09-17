# F0 Frontend Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the current backend contract safe and coherent for an Expo/React Native frontend.

**Architecture:** Centralize donor/request discovery and request creation in service functions shared by REST and MCP. Add the two missing lifecycle endpoints, keep persistence unchanged, and normalize notification JSON only at the response boundary.

**Tech Stack:** Python 3.12, FastAPI, SQLModel/SQLAlchemy, Pydantic v2, PostgreSQL 16, SQLite tests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-18-f0-frontend-readiness.md`

## Global Constraints

- No database migration in F0.
- Preserve existing request privacy rules and P3.1 transactional outbox semantics.
- Acceptance remains the authoritative final capacity/eligibility check.
- Every production behavior change starts with a failing regression test.

---

### Task 1: Shared RBC-compatible donor discovery

**Files:**
- Create: `app/services/discovery_service.py`
- Modify: `app/api/v1/donors.py`
- Modify: `app/mcp_tools/tools.py`
- Test: `tests/test_api_donors.py`
- Test: `tests/test_api_chat.py`

**Interfaces:**
- Produce `find_compatible_donors(session, *, viewer, recipient_blood_group, latitude, longitude, radius_km, division=None, district=None, upazila=None) -> list[tuple[User, float]]`.

- [ ] Add a test proving an O+ donor appears for an A+ recipient-group search, while an incompatible donor does not.
- [ ] Run focused donor tests and verify RED because exact-group filtering excludes the compatible non-identical donor.
- [ ] Implement the shared discovery function using `is_blood_compatible()` and `is_eligible()`; switch REST and MCP donor lookup to it.
- [ ] Run donor/chat tests and verify GREEN.
- [ ] Commit `feat: unify compatible donor discovery`.

### Task 2: Nearby feed contains only acceptable requests

**Files:**
- Modify: `app/services/discovery_service.py`
- Modify: `app/api/v1/blood_requests.py`
- Modify: `app/mcp_tools/tools.py`
- Test: `tests/test_api_blood_requests.py`
- Test: `tests/test_api_chat.py`

**Interfaces:**
- Produce `find_acceptable_nearby_requests(session, *, donor, latitude, longitude, radius_km) -> list[tuple[BloodRequest, float]]`.

- [ ] Add tests proving incompatible, unavailable, recent-donation, and incomplete-profile donors receive no impossible nearby cards, while an eligible compatible donor does.
- [ ] Verify focused tests RED against the current broad nearby query.
- [ ] Implement shared nearby discovery and use it from REST and MCP.
- [ ] Verify focused tests GREEN.
- [ ] Commit `feat: filter nearby requests by donor readiness`.

### Task 3: Donor active commitments endpoint

**Files:**
- Modify: `app/schemas/blood_request.py`
- Modify: `app/services/request_service.py`
- Modify: `app/api/v1/blood_requests.py`
- Test: `tests/test_api_commitments.py`

**Interfaces:**
- Add `DonorCommitmentResponse(commitment: CommitmentResponse, request: BloodRequestResponse)`.
- Add `GET /api/v1/blood-requests/commitments/mine?limit=&offset=` returning `PaginatedResponse[DonorCommitmentResponse]`.

- [ ] Add test: after donor accepts, active list contains that commitment and privacy-authorized request details; unrelated donor cannot see it; withdrawal removes it from active list.
- [ ] Verify RED because route does not exist.
- [ ] Implement paginated donor-owned `Committed` lookup and response building.
- [ ] Verify GREEN.
- [ ] Commit `feat: add active donor commitments endpoint`.

### Task 4: Recipient release/no-show action

**Files:**
- Modify: `app/db/models.py`
- Modify: `app/services/commitment_service.py`
- Modify: `app/services/request_service.py`
- Modify: `app/api/v1/blood_requests.py`
- Test: `tests/test_api_commitments.py`
- Test: `tests/test_commitment_service.py`

**Interfaces:**
- Add `NotificationType.COMMITMENT_RELEASED = "Commitment Released"`.
- Add `release_commitment(session, request_id, commitment_id, recipient) -> BloodRequest`.
- Add `POST /api/v1/blood-requests/{request_id}/commitments/{commitment_id}/release`.

- [ ] Add tests proving recipient can release one committed unit, slot/capacity reopens, other commitments are untouched, donor is notified, non-recipient is forbidden, and completed commitment returns conflict.
- [ ] Verify RED.
- [ ] Implement one transaction: validate recipient/request/commitment, cancel committed row, clear slot, recalculate status, audit, create durable notification, commit.
- [ ] Verify GREEN including request lifecycle regressions.
- [ ] Commit `feat: let recipients release donor commitments`.

### Task 5: Require coordinates and unify durable request creation

**Files:**
- Modify: `app/schemas/blood_request.py`
- Modify: `app/services/request_service.py`
- Modify: `app/api/v1/blood_requests.py`
- Modify: `app/mcp_tools/tools.py`
- Modify: `app/services/chat_agent.py`
- Test: `tests/test_api_blood_requests.py`
- Test: `tests/test_api_chat.py`
- Test: `tests/test_notification_outbox.py`

**Interfaces:**
- `BloodRequestCreate.latitude: float` and `.longitude: float` are required and range validated.
- Add `create_blood_request(session, user, validated: BloodRequestCreate) -> BloodRequest` that atomically writes request + audit + `blood_request_created` outbox event.

- [ ] Add tests rejecting missing latitude/longitude for REST and MCP validation.
- [ ] Add test proving chat/MCP-created request creates exactly one P3.1 outbox event and no direct notification fan-out dependency.
- [ ] Verify RED.
- [ ] Implement shared service creation; make REST and MCP delegate to it; mark MCP coordinates required and update rule-based collection copy to request hospital coordinates.
- [ ] Verify GREEN.
- [ ] Commit `feat: unify durable request creation`.

### Task 6: Frontend-friendly notification data

**Files:**
- Modify: `app/schemas/notification.py`
- Modify: `app/api/v1/notifications.py` only if explicit conversion is preferable to schema validation
- Test: `tests/test_api_notifications.py`

**Interfaces:**
- `NotificationResponse.data: dict | None`.

- [ ] Add tests proving valid stored JSON is returned as an object and invalid historical JSON becomes null without HTTP 500.
- [ ] Verify RED because current API exposes a string.
- [ ] Add a Pydantic field validator or endpoint mapper that safely parses stored JSON.
- [ ] Verify GREEN.
- [ ] Commit `feat: return structured notification data`.

### Task 7: Regression, CI, and contract documentation

**Files:**
- Modify: `.github/workflows/tests.yml` if required to include new F0-focused PostgreSQL tests
- Modify: `README.md`
- Test: full suite

- [ ] Update README frontend/API contract notes: compatible donor semantics, required request coordinates, active commitments route, release route, notification object data.
- [ ] Run `python -m compileall -q app tests scripts alembic` in CI.
- [ ] Run full `pytest -q`.
- [ ] Run PostgreSQL 16 migration + F0/request/commitment subset.
- [ ] Review branch diff for privacy regressions, duplicated discovery logic, stale best-effort AI fan-out, placeholders, and obsolete imports.
- [ ] Commit `docs: document frontend-ready API contract`.

### Task 8: Integration gate

- [ ] Open a PR from `f0-frontend-readiness` to `main`.
- [ ] Confirm current PR head CI is green.
- [ ] Review unresolved threads and mergeability.
- [ ] Squash merge with expected-head SHA guard.
- [ ] Verify `main` points to the squash commit.
- [ ] Verify permanent post-merge SQLite and PostgreSQL workflow success before declaring F0 complete.
