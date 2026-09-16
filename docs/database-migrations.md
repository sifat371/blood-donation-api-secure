# Database migrations

Alembic is the canonical schema history for P2 and later. SQLite remains
supported for local development, tests, and existing installations; PostgreSQL
is the intended production database.

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
Alembic, schema changes are applied explicitly:

```bash
uv run alembic upgrade head
```

The P1 baseline revision creates the schema that existed immediately before P2,
including the legacy `blood_requests.accepted_by` column. Later revisions
transform that schema and its data forward.

## PostgreSQL

PostgreSQL uses the same Alembic revision history and does not run SQLite
`PRAGMA`, `sqlite_master`, or legacy startup migration code. Configure a
`postgresql+psycopg://...` `DATABASE_URL`, provision the database, and run:

```bash
uv run alembic upgrade head
```

P2 CI will exercise fresh PostgreSQL upgrades before the branch is eligible to
merge.

## Application startup boundary

P1 still contains legacy startup migration code while P2 is being built. The
final P2 rollout removes runtime schema mutation: production/staging schema
evolution belongs to Alembic, and the API will fail clearly when the configured
database is unversioned or behind Alembic head.
