# Database migrations

Alembic is the canonical schema history for P2 and later. SQLite remains
supported for local development, tests, and existing installations; PostgreSQL
16+ is the production CI target.

## Existing P1 SQLite installation

P1 databases predate Alembic and therefore do not have an `alembic_version`
table. Do **not** stamp such a database manually. Use the guarded helper:

```bash
uv run python scripts/migrate_existing_db.py
```

The helper:

1. requires the configured `DATABASE_URL` to point to an existing SQLite file;
2. inspects the database and refuses unknown/incomplete schemas;
3. records critical row counts;
4. creates a timestamped `*.pre-p2-*.db` backup beside the source database;
5. stamps the verified database at the immutable P1 baseline revision;
6. runs `alembic upgrade head`;
7. verifies the final Alembic revision and preserved row counts.

The backup is never deleted automatically. If an upgrade fails, keep the backup
and investigate before retrying.

Already-versioned SQLite databases are not stamped again; the helper simply
runs the normal upgrade path.

## New or managed databases

For a new SQLite/PostgreSQL database, or any database that is already managed by
Alembic, schema changes are applied explicitly before application startup:

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The P1 baseline revision creates the schema that existed immediately before P2,
including the legacy `blood_requests.accepted_by` column. Later revisions
transform that schema and its data forward.

## PostgreSQL

Configure a psycopg URL such as:

```text
DATABASE_URL=postgresql+psycopg://app_user:secret@db.example.com/blood_app
```

Provision the empty database, then run:

```bash
uv run alembic upgrade head
```

PostgreSQL uses the same Alembic revision history and never runs SQLite
`PRAGMA`, `sqlite_master`, `SQLModel.metadata.create_all`, or the retired legacy
startup migration path. GitHub Actions provisions PostgreSQL 16, upgrades a
fresh database to head, and runs the migration/commitment/request regression
subset before P2 is eligible to merge.

## Application startup boundary

Application startup is verification-only:

1. validate runtime configuration;
2. call `assert_schema_at_head(engine)`;
3. initialise Firebase on a best-effort basis;
4. serve requests.

Startup does **not** create, alter, stamp, or upgrade tables. If the database is
unversioned or its `alembic_version` is behind the repository head, startup
raises a clear error and the operator must migrate first.

That separation is deliberate: schema evolution is a deployment action, not an
API side effect. It also prevents two application replicas from racing to alter
the same production database during startup.

## Checking revision state

```bash
uv run alembic current
uv run alembic heads
```

For a deployable database, `current` must resolve to the same revision as the
single configured Alembic head.
