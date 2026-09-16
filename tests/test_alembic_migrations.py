"""P2 database migration contract tests."""

from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.schema_version import assert_schema_at_head
from scripts.migrate_existing_db import migrate_existing_sqlite, verify_p1_schema


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _make_unversioned_p1_database(path: Path) -> None:
    """Create a frozen P1 database fixture without relying on current models."""
    url = f"sqlite:///{path}"
    command.upgrade(_config(url), "0001_p1_baseline")
    now = datetime(2026, 9, 16, 12, 0, 0)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, email_verified, auth_provider, is_available, created_at, updated_at) "
                "VALUES (:id, :name, :email, :verified, :provider, :available, :created, :updated)"
            ),
            {
                "id": 41,
                "name": "Legacy Recipient",
                "email": "legacy@example.com",
                "verified": True,
                "provider": "google",
                "available": True,
                "created": now,
                "updated": now,
            },
        )
        conn.execute(
            text(
                "INSERT INTO blood_requests "
                "(id, recipient_id, patient_name, blood_group, units, hospital_name, needed_date, "
                "contact_number, status, created_at, updated_at) "
                "VALUES (:id, :recipient_id, :patient_name, :blood_group, :units, :hospital_name, "
                ":needed_date, :contact_number, :status, :created, :updated)"
            ),
            {
                "id": 91,
                "recipient_id": 41,
                "patient_name": "Legacy Patient",
                "blood_group": "O+",
                "units": 2,
                "hospital_name": "Legacy Hospital",
                "needed_date": "2026-09-20",
                "contact_number": "+8801700000041",
                "status": "Pending",
                "created": now,
                "updated": now,
            },
        )
        conn.execute(text("DROP TABLE alembic_version"))


def test_fresh_sqlite_upgrade_reaches_p1_baseline(tmp_path: Path):
    db = tmp_path / "fresh.db"
    url = f"sqlite:///{db}"

    command.upgrade(_config(url), "0001_p1_baseline")

    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "users",
        "fcm_tokens",
        "blood_requests",
        "donation_history",
        "notifications",
        "refresh_tokens",
        "email_verifications",
        "audit_logs",
        "user_locations",
        "conversation_history",
        "alembic_version",
    } <= tables
    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "0001_p1_baseline"


def test_schema_verifier_rejects_unversioned_database(tmp_path: Path):
    db = tmp_path / "unversioned.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))

    with pytest.raises(RuntimeError, match="not Alembic-versioned"):
        assert_schema_at_head(engine)


def test_verify_p1_schema_rejects_unknown_unversioned_schema(tmp_path: Path):
    db = tmp_path / "broken.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))

    with pytest.raises(RuntimeError, match="P1 schema verification failed"):
        verify_p1_schema(engine)


def test_existing_p1_sqlite_is_backed_up_stamped_and_preserved(tmp_path: Path):
    db = tmp_path / "legacy.db"
    _make_unversioned_p1_database(db)

    backup = migrate_existing_sqlite(db)

    assert backup is not None
    assert backup.exists()
    assert backup != db

    migrated = create_engine(f"sqlite:///{db}")
    with migrated.connect() as conn:
        assert conn.execute(text("SELECT name FROM users WHERE id = 41")).scalar_one() == "Legacy Recipient"
        assert conn.execute(text("SELECT patient_name FROM blood_requests WHERE id = 91")).scalar_one() == "Legacy Patient"
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001_p1_baseline"

    untouched_backup = create_engine(f"sqlite:///{backup}")
    assert "alembic_version" not in inspect(untouched_backup).get_table_names()
    with untouched_backup.connect() as conn:
        assert conn.execute(text("SELECT name FROM users WHERE id = 41")).scalar_one() == "Legacy Recipient"
