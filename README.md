# Blood Donation API — Backend

FastAPI + SQLModel with Alembic-managed SQLite/PostgreSQL schemas. Managed with [uv](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
cp .env.example .env
```

Then edit `.env`. The only value you must set for local development is
`SECRET_KEY`; everything else has a working default. See `.env.example` for what
each variable does — `GOOGLE_CLIENT_ID`, `GEMINI_API_KEY` and
`FCM_CREDENTIALS_PATH` are all optional, and the app degrades cleanly without
them (dev login instead of Google, rule-based chat instead of Gemini, in-app
notifications without push).

`.env` and `app/serviceAccount.json` are gitignored. Never commit either.

### Prepare the database

Alembic is the schema owner. For a new SQLite/PostgreSQL database, or an already
versioned database, run:

```bash
uv run alembic upgrade head
```

If you are upgrading an existing unversioned P1 SQLite database, use the guarded
backup/verification helper instead:

```bash
uv run python scripts/migrate_existing_db.py
```

The API refuses to start when the configured database is unversioned or behind
Alembic head. Startup never runs `create_all()` or implicit schema migrations.
See `docs/database-migrations.md` for the deployment paths and backup behavior.

## Run

For the web build or the iOS simulator, after the database is at Alembic head:

```bash
uv run uvicorn app.main:app --reload
```

**For a physical phone or the Android emulator, bind to all interfaces:**

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Without `--host 0.0.0.0`, uvicorn listens on `127.0.0.1` only and the phone gets
a connection refused no matter how the app is configured — "it works on
localhost" says nothing about whether another device can reach it.

Find the address the phone should use:

```bash
hostname -I | awk '{print $1}'
```

The app resolves the backend URL automatically from the Metro dev-server host,
which is this same LAN IP, so a device on the same Wi-Fi usually needs no
configuration. Override it with `EXPO_PUBLIC_API_URL` in `frontend/.env` when
that isn't true — a different machine, a non-default port, a tunnel, staging.
The Android emulator reaches the host machine at `http://10.0.2.2:8000`.

If the phone still can't connect, the firewall is the next thing to check:

```bash
sudo ufw allow 8000/tcp
```

Interactive API docs: <http://localhost:8000/docs>.
Config sanity check: <http://localhost:8000/health> reports `environment` and
whether `push_notifications` initialised.

## Tests

```bash
uv run pytest
```

```bash
uv run pytest -q tests/test_e2e_multi_user.py
```

The normal unit/API suite uses isolated test databases and never touches
`blood_donation.db`. GitHub Actions additionally provisions PostgreSQL 16, runs
`alembic upgrade head`, and executes the migration/commitment/request regression
subset against the production-target database configuration.

## Smoke check a running server

`pytest` covers the app in-process. To verify the address a real device will
actually use, start the server and hit it over HTTP:

```bash
uv run python scripts/smoke_check.py
```

```bash
API_BASE_URL=http://192.168.0.104:8000 uv run python scripts/smoke_check.py
```

It logs in, then exercises profile, donor search, notifications, chat, chat
history, my/nearby requests and donation history — exiting non-zero on the first
non-2xx. Running it with the phone's `API_BASE_URL` from a *second* machine is
the quickest way to confirm the bind and the firewall are right before
installing the app.

## Maintenance scripts

```bash
uv run python scripts/fix_transposed_coordinates.py
```

Reports rows whose latitude/longitude look swapped (a latitude outside
Bangladesh's ~20.5–26.7 band paired with a plausible longitude). Dry-run by
default; `--apply` writes a timestamped backup of the database first.

## Production safety

When `ENVIRONMENT=production`, startup fails unless `SECRET_KEY` is non-default,
`CORS_ORIGINS` is an explicit allow-list, and SMTP delivery is configured. Google
sign-in is disabled unless `GOOGLE_CLIENT_ID` is configured. Public donor search
does not expose phone numbers, and active request discovery redacts patient,
contact, exact-location, and account identifiers until the donor has an active
commitment.

Donor commitment is enforced server-side: the donor must have a complete donor
profile, be available, satisfy the recorded 90-day donation interval, and have a
red-cell-compatible blood group. Final medical eligibility and transfusion
compatibility still require screening by qualified healthcare/blood-bank staff.

### Time and request expiry

Calendar-day rules use `BUSINESS_TIMEZONE` (default `Asia/Dhaka`). New requests
may target today or a future date; overdue open requests are marked `Expired`
and are no longer shown to donors or eligible for new commitments. Stored
application timestamps remain UTC for database compatibility.

### Database migration boundary

Alembic is mandatory for both SQLite and PostgreSQL. Deployments must migrate the
database before starting the API:

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Existing unversioned P1 SQLite installations must use
`uv run python scripts/migrate_existing_db.py` once so the schema is verified,
backed up, stamped at the P1 baseline, and upgraded safely. The API itself never
mutates database structure during startup.
