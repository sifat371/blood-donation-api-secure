import asyncio

import pytest

import app.main as main_module
from app.db.database import _engine_kwargs


def test_sqlite_engine_disables_same_thread_check():
    assert _engine_kwargs("sqlite:///./test.db") == {
        "connect_args": {"check_same_thread": False}
    }


def test_non_sqlite_engine_does_not_receive_sqlite_connect_args():
    assert _engine_kwargs("postgresql+psycopg://user:pass@db.example/app") == {}


def _run_lifespan() -> None:
    async def _run():
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(_run())


def test_startup_verifies_alembic_schema_without_mutating_it(monkeypatch):
    calls: list[object] = []

    monkeypatch.setattr(main_module, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(main_module, "init_firebase", lambda: None)
    monkeypatch.setattr(
        main_module,
        "assert_schema_at_head",
        lambda engine: calls.append(engine),
        raising=False,
    )

    def mutation_is_forbidden(*args, **kwargs):
        raise AssertionError("application startup must not mutate database schema")

    # These names exist on P1/P2-before-Task-7. Keeping the patches tolerant
    # makes the test remain valid after the legacy imports are removed.
    monkeypatch.setattr(
        main_module, "create_db_and_tables", mutation_is_forbidden, raising=False
    )
    monkeypatch.setattr(main_module, "run_migrations", mutation_is_forbidden, raising=False)

    _run_lifespan()

    assert calls == [main_module.engine]


def test_startup_fails_closed_when_database_is_behind_alembic_head(monkeypatch):
    monkeypatch.setattr(main_module, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(main_module, "init_firebase", lambda: None)
    monkeypatch.setattr(main_module, "create_db_and_tables", lambda: None, raising=False)
    monkeypatch.setattr(main_module, "run_migrations", lambda: [], raising=False)

    def behind_head(_engine):
        raise RuntimeError(
            "Database schema revision '0001_p1_baseline' is behind expected "
            "Alembic head '0002_multi_donor'; run 'uv run alembic upgrade head'"
        )

    monkeypatch.setattr(
        main_module, "assert_schema_at_head", behind_head, raising=False
    )

    with pytest.raises(RuntimeError, match="uv run alembic upgrade head"):
        _run_lifespan()
