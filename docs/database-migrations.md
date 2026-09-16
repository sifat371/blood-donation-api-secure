# Database migration boundary

The application currently supports SQLite for local development and tests. The
legacy startup migration module (`app/db/migrations.py`) contains SQLite-specific
`PRAGMA`/`sqlite_master` logic and is intentionally executed **only** when the
active SQLAlchemy dialect is SQLite.

`app/db/database.py` is now dialect-aware: SQLite receives
`check_same_thread=False`; PostgreSQL-style URLs do not receive SQLite driver
arguments.

## PostgreSQL production handoff

Before deploying this application on PostgreSQL, introduce a versioned Alembic
migration history from the current SQLModel metadata and make Alembic the only
production schema-change mechanism. Do not rely on `create_all()` or the legacy
SQLite migration module to evolve an existing PostgreSQL database.

A safe rollout sequence is:

1. Provision an empty PostgreSQL database and the required driver in the deploy image.
2. Generate/review an Alembic baseline matching the current SQLModel tables.
3. Apply the baseline in staging and run the full API/test suite against PostgreSQL.
4. Convert every future schema change into a reviewed Alembic revision.
5. Disable any production startup behavior that attempts implicit schema mutation.

This boundary keeps today's SQLite workflow working while preventing a
`DATABASE_URL` switch from silently running SQLite-specific migration SQL on a
non-SQLite database.
