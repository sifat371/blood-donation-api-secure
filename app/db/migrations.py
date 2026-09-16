"""Historical pre-Alembic SQLite migration helpers.

This module is retained only for understanding/recovering databases created by
older P1 development builds. It is **not** part of application startup and must
not be imported from ``app.main``. P2 and later use Alembic exclusively for
schema ownership; existing unversioned P1 SQLite files must be migrated through
``scripts/migrate_existing_db.py``.

The functions below intentionally remain SQLite-specific and narrowly additive
so an operator investigating an old database can reproduce the former P1
behavior without changing the P2 runtime contract.
"""

import logging
from typing import Iterable

from sqlalchemy import text
from sqlmodel import Session

from app.db.database import engine

logger = logging.getLogger(__name__)


def _columns(session: Session, table: str) -> set[str]:
    rows = session.exec(text(f"PRAGMA table_info({table})")).all()
    return {row[1] for row in rows}


def _tables(session: Session) -> set[str]:
    rows = session.exec(
        text("SELECT name FROM sqlite_master WHERE type='table'")
    ).all()
    return {row[0] for row in rows}


def _add_column(session: Session, table: str, column: str, ddl: str) -> bool:
    """Add ``column`` if absent; historical P1 behavior only."""
    if column in _columns(session, table):
        return False
    session.exec(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    logger.info("Legacy migration: added %s.%s", table, column)
    return True


def _run_users_email_verification(session: Session) -> Iterable[str]:
    """Reproduce the former P1 email-verification additive migration."""
    applied: list[str] = []

    added_verified = _add_column(
        session, "users", "email_verified", "BOOLEAN NOT NULL DEFAULT 0"
    )
    if added_verified:
        applied.append("users.email_verified")

    if _add_column(session, "users", "email_verified_at", "DATETIME"):
        applied.append("users.email_verified_at")

    if _add_column(
        session, "users", "auth_provider", "VARCHAR NOT NULL DEFAULT 'google'"
    ):
        applied.append("users.auth_provider")

    if added_verified:
        result = session.exec(
            text(
                "UPDATE users SET email_verified = 1, "
                "email_verified_at = COALESCE(email_verified_at, created_at)"
            )
        )
        logger.info(
            "Legacy migration: grandfathered %s pre-existing account(s) as verified",
            result.rowcount,
        )
        applied.append(f"backfill:{result.rowcount}-existing-users-verified")

    session.exec(
        text(
            "UPDATE users SET auth_provider = CASE "
            "  WHEN google_id IS NOT NULL AND hashed_password IS NOT NULL "
            "    THEN 'both' "
            "  WHEN hashed_password IS NOT NULL THEN 'password' "
            "  ELSE 'google' END"
        )
    )

    return applied


def run_migrations() -> list[str]:
    """Reproduce the old P1 SQLite-only runtime migration when invoked manually.

    P2 application startup never calls this function. Use Alembic for every
    current schema change, and use ``scripts/migrate_existing_db.py`` to safely
    bootstrap an unversioned P1 SQLite database into Alembic history.
    """
    if engine.dialect.name != "sqlite":
        logger.info(
            "Legacy migration helper skipped for database dialect %s; use Alembic",
            engine.dialect.name,
        )
        return []

    applied: list[str] = []
    with Session(engine) as session:
        if "users" not in _tables(session):
            return applied
        applied.extend(_run_users_email_verification(session))
        session.commit()

    if applied:
        logger.info("Legacy migrations applied: %s", ", ".join(applied))
    return applied
