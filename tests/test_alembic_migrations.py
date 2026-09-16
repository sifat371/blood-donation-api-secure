"""P2 database migration contract tests."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.schema_version import assert_schema_at_head, expected_head
from scripts.migrate_existing_db import migrate_existing_sqlite, verify_p1_schema


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _make_unversioned_p1_database(path: Path) -> None:
    """Create a frozen P1 database fixture without relying on current models."""
    url = f"sqlite:///{path}"
    command.upgrade(_config(url), "0001_p1_baseline")
    now = "2026-09-16 12:00:00"
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


def _make_versioned_p1_transformation_fixture(path: Path) -> None:
    """Create representative P1 request states for revision-0002 testing."""
    url = f"sqlite:///{path}"
    command.upgrade(_config(url), "0001_p1_baseline")
    engine = create_engine(url)
    now = "2026-09-16 12:00:00"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, email_verified, auth_provider, is_available, created_at, updated_at) "
                "VALUES (:id, :name, :email, 1, 'google', 1, :created, :updated)"
            ),
            [
                {
                    "id": user_id,
                    "name": f"Legacy User {user_id}",
                    "email": f"legacy{user_id}@example.com",
                    "created": now,
                    "updated": now,
                }
                for user_id in range(1, 8)
            ],
        )
        conn.execute(
            text(
                "INSERT INTO blood_requests "
                "(id, recipient_id, patient_name, blood_group, units, hospital_name, needed_date, "
                "contact_number, status, accepted_by, created_at, updated_at) "
                "VALUES (:id, 1, :patient_name, 'O+', :units, 'Legacy Hospital', '2026-09-20', "
                "'+8801700000001', :status, :accepted_by, :created, :updated)"
            ),
            [
                {"id": 101, "patient_name": "Pending", "units": 2, "status": "Pending", "accepted_by": None, "created": now, "updated": now},
                {"id": 102, "patient_name": "Accepted One", "units": 1, "status": "Accepted", "accepted_by": 2, "created": now, "updated": now},
                {"id": 103, "patient_name": "Accepted Multi", "units": 3, "status": "Accepted", "accepted_by": 3, "created": now, "updated": now},
                {"id": 104, "patient_name": "Completed One", "units": 1, "status": "Completed", "accepted_by": 4, "created": now, "updated": now},
                {"id": 105, "patient_name": "Completed Multi", "units": 2, "status": "Completed", "accepted_by": 5, "created": now, "updated": now},
                {"id": 106, "patient_name": "Cancelled", "units": 2, "status": "Cancelled", "accepted_by": 6, "created": now, "updated": now},
                {"id": 107, "patient_name": "Expired", "units": 1, "status": "Expired", "accepted_by": 7, "created": now, "updated": now},
            ],
        )
        conn.execute(
            text(
                "INSERT INTO donation_history "
                "(id, donor_id, request_id, date, blood_group, status, created_at) "
                "VALUES (:id, :donor_id, :request_id, '2026-09-16', 'O+', 'Completed', :created)"
            ),
            [
                {"id": 201, "donor_id": 4, "request_id": 104, "created": now},
                {"id": 202, "donor_id": 5, "request_id": 105, "created": now},
            ],
        )


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
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == expected_head()

    untouched_backup = create_engine(f"sqlite:///{backup}")
    assert "alembic_version" not in inspect(untouched_backup).get_table_names()
    with untouched_backup.connect() as conn:
        assert conn.execute(text("SELECT name FROM users WHERE id = 41")).scalar_one() == "Legacy Recipient"


def test_multi_donor_revision_preserves_and_transforms_legacy_request_state(tmp_path: Path):
    db = tmp_path / "legacy-states.db"
    url = f"sqlite:///{db}"
    _make_versioned_p1_transformation_fixture(db)

    command.upgrade(_config(url), "head")

    engine = create_engine(url)
    inspector = inspect(engine)
    assert "donation_commitments" in inspector.get_table_names()
    request_columns = {column["name"] for column in inspector.get_columns("blood_requests")}
    assert "accepted_by" not in request_columns
    assert "legacy_completion_incomplete" in request_columns
    history_columns = {column["name"] for column in inspector.get_columns("donation_history")}
    assert "commitment_id" in history_columns

    with engine.connect() as conn:
        statuses = dict(conn.execute(text("SELECT id, status FROM blood_requests")))
        legacy_flags = dict(
            conn.execute(text("SELECT id, legacy_completion_incomplete FROM blood_requests"))
        )
        commitments = {
            (request_id, donor_id): (commitment_id, status)
            for commitment_id, request_id, donor_id, status in conn.execute(
                text("SELECT id, request_id, donor_id, status FROM donation_commitments")
            )
        }

        assert statuses[101] == "Pending"
        assert statuses[102] == "Fully Committed"
        assert statuses[103] == "Partially Committed"
        assert statuses[104] == "Completed"
        assert statuses[105] == "Completed"
        assert statuses[106] == "Cancelled"
        assert statuses[107] == "Expired"

        assert bool(legacy_flags[105]) is True
        assert all(not bool(legacy_flags[request_id]) for request_id in (101, 102, 103, 104, 106, 107))

        assert commitments[(102, 2)][1] == "Committed"
        assert commitments[(103, 3)][1] == "Committed"
        assert commitments[(104, 4)][1] == "Completed"
        assert commitments[(105, 5)][1] == "Completed"
        assert commitments[(106, 6)][1] == "Cancelled"
        assert commitments[(107, 7)][1] == "Cancelled"

        history_links = dict(
            conn.execute(text("SELECT request_id, commitment_id FROM donation_history"))
        )
        assert history_links[104] == commitments[(104, 4)][0]
        assert history_links[105] == commitments[(105, 5)][0]
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0002_multi_donor"
