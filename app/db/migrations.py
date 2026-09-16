"""
Additive schema migrations.

`SQLModel.metadata.create_all` creates missing *tables* but never adds a column
to a table that already exists, so a development database created before the
email/password columns existed would keep working right up until the first query
touched one of them. The project has no Alembic setup, so this module applies the
few additive changes by hand, idempotently, at startup.

Rules kept deliberately narrow:
  * only ADD COLUMN — nothing is dropped, renamed, or retyped;
  * every step checks the current schema first, so a second run is a no-op;
  * no user rows are deleted or rewritten beyond the one documented backfill.
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
    """Add `column` if absent. Returns True when the column was just created."""
    if column in _columns(session, table):
        return False
    session.exec(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    logger.info("Migration: added %s.%s", table, column)
    return True


def _run_users_email_verification(session: Session) -> Iterable[str]:
    """
    Add the email-verification columns to `users`.

    Backfill policy for accounts that predate this migration: they are marked
    verified. Every existing account was created either through Google sign-in
    (where Google has already verified the address) or through the development
    login, and defaulting them to unverified would lock real people out of
    working accounts to enforce a check that did not exist when they signed up.
    New accounts start unverified — the default on the column — so the guarantee
    holds from here forward.
    """
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
        # Only on the run that created the column, so a later manual
        # "unverify" is never silently undone.
        result = session.exec(
            text(
                "UPDATE users SET email_verified = 1, "
                "email_verified_at = COALESCE(email_verified_at, created_at)"
            )
        )
        logger.info(
            "Migration: grandfathered %s pre-existing account(s) as verified",
            result.rowcount,
        )
        applied.append(f"backfill:{result.rowcount}-existing-users-verified")

    # Keep the summary column honest for accounts that already have a password
    # (none today, but the migration must not lie if one exists).
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
    """Apply legacy additive migrations on SQLite only.

    PostgreSQL and other production-grade databases must use versioned schema
    migrations (Alembic). Skipping the SQLite PRAGMA path there is deliberate: a
    DATABASE_URL change must never execute SQLite-specific SQL against another
    dialect.
    """
    if engine.dialect.name != "sqlite":
        logger.info(
            "Skipping legacy SQLite migrations for database dialect %s; "
            "use versioned migrations for schema changes",
            engine.dialect.name,
        )
        return []

    applied: list[str] = []
    with Session(engine) as session:
        if "users" not in _tables(session):
            # Fresh database: create_all already produced the current schema.
            return applied
        applied.extend(_run_users_email_verification(session))
        session.commit()

    if applied:
        logger.info("Migrations applied: %s", ", ".join(applied))
    return applied
