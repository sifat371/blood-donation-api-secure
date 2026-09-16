"""Safely bootstrap an existing unversioned P1 SQLite database into Alembic.

Existing P1 installations predate the Alembic version table. This helper
verifies that schema before stamping it, creates a byte-for-byte backup, then
lets Alembic perform all subsequent upgrades.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

from app.core.config import settings
from app.db.schema_version import expected_head

P1_BASELINE_REVISION = "0001_p1_baseline"

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "users": {
        "id",
        "name",
        "email",
        "email_verified",
        "auth_provider",
        "blood_group",
        "last_donation_date",
        "is_available",
        "created_at",
        "updated_at",
    },
    "fcm_tokens": {"id", "user_id", "token", "device_info", "created_at"},
    "blood_requests": {
        "id",
        "recipient_id",
        "patient_name",
        "blood_group",
        "units",
        "needed_date",
        "contact_number",
        "status",
        "accepted_by",
        "created_at",
        "updated_at",
    },
    "donation_history": {
        "id",
        "donor_id",
        "request_id",
        "date",
        "blood_group",
        "status",
        "created_at",
    },
    "notifications": {"id", "user_id", "type", "is_read", "created_at"},
    "refresh_tokens": {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "revoked",
        "created_at",
    },
    "email_verifications": {
        "id",
        "user_id",
        "email",
        "code_hash",
        "expires_at",
        "consumed",
        "attempts",
        "created_at",
    },
    "audit_logs": {"id", "user_id", "action", "entity", "created_at"},
    "user_locations": {"id", "user_id", "latitude", "longitude", "recorded_at"},
    "conversation_history": {"id", "user_id", "role", "content", "created_at"},
}


def _alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def verify_p1_schema(engine: Engine) -> None:
    """Reject anything that cannot be confidently identified as the P1 schema."""
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    problems: list[str] = []

    for table, required in REQUIRED_COLUMNS.items():
        if table not in actual_tables:
            problems.append(f"missing table {table!r}")
            continue
        actual_columns = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(required - actual_columns)
        if missing:
            problems.append(f"table {table!r} is missing columns: {', '.join(missing)}")

    if problems:
        raise RuntimeError("P1 schema verification failed: " + "; ".join(problems))


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: int(conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one())
            for table in REQUIRED_COLUMNS
        }


def backup_sqlite_database(source: Path) -> Path:
    """Create a timestamped pre-P2 copy beside the SQLite database."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = source.with_name(f"{source.stem}.pre-p2-{timestamp}{source.suffix}")
    shutil.copy2(source, backup)
    return backup


def migrate_existing_sqlite(source: Path) -> Path | None:
    """Version and upgrade one existing SQLite file.

    Returns the newly created backup path when bootstrapping an unversioned P1
    database. Already-versioned databases are simply upgraded and return None.
    """
    source = Path(source).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise RuntimeError(f"SQLite database file does not exist: {source}")

    url = f"sqlite:///{source}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    tables = set(inspect(engine).get_table_names())
    backup: Path | None = None
    before_counts: dict[str, int] | None = None

    if "alembic_version" not in tables:
        verify_p1_schema(engine)
        before_counts = _row_counts(engine)
        engine.dispose()

        backup = backup_sqlite_database(source)
        command.stamp(_alembic_config(url), P1_BASELINE_REVISION)
    else:
        engine.dispose()

    command.upgrade(_alembic_config(url), "head")

    verified_engine = create_engine(url, connect_args={"check_same_thread": False})
    try:
        with verified_engine.connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        head = expected_head()
        if current != head:
            raise RuntimeError(
                f"Migration finished at revision {current!r}, expected Alembic head {head!r}"
            )

        if before_counts is not None:
            after_counts = _row_counts(verified_engine)
            changed = {
                table: (before_counts[table], after_counts[table])
                for table in before_counts
                if before_counts[table] != after_counts[table]
            }
            if changed:
                raise RuntimeError(f"Migration row-count verification failed: {changed}")
    finally:
        verified_engine.dispose()

    return backup


def _configured_sqlite_path() -> Path:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        raise RuntimeError(
            "migrate_existing_db.py is only for existing SQLite installations; "
            "use 'uv run alembic upgrade head' for PostgreSQL"
        )
    if not url.database or url.database == ":memory:":
        raise RuntimeError("Configured DATABASE_URL must point to an existing SQLite file")
    return Path(url.database)


def main() -> None:
    source = _configured_sqlite_path()
    backup = migrate_existing_sqlite(source)
    if backup is None:
        print(f"Database already versioned; upgraded to {expected_head()}: {source}")
    else:
        print(f"Backup created: {backup}")
        print(f"Database upgraded to {expected_head()}: {source}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"Migration failed: {exc}") from exc
