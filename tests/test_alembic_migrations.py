"""P2 database migration contract tests.

These tests intentionally land before the Alembic implementation. The first
RED cycle proves a fresh database must be constructible from the P1 baseline
revision and that runtime schema verification rejects unversioned/behind
schemas instead of mutating them.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.schema_version import assert_schema_at_head


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


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
