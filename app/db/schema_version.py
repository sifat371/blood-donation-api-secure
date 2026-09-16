"""Alembic schema-version guard used by application startup."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text


def expected_head() -> str:
    """Return the single configured Alembic head revision."""
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    if head is None:
        raise RuntimeError("Alembic has no configured head revision")
    return head


def assert_schema_at_head(engine) -> None:
    """Fail closed when the configured database is unversioned or behind."""
    if "alembic_version" not in inspect(engine).get_table_names():
        raise RuntimeError(
            "Database is not Alembic-versioned; run the migration/bootstrap "
            "command before starting the API"
        )

    with engine.connect() as conn:
        current = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()

    head = expected_head()
    if current != head:
        raise RuntimeError(
            f"Database schema revision {current!r} is behind expected Alembic "
            f"head {head!r}; run 'uv run alembic upgrade head'"
        )
