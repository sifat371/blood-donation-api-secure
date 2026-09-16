from app.db.database import _engine_kwargs


def test_sqlite_engine_disables_same_thread_check():
    assert _engine_kwargs("sqlite:///./test.db") == {
        "connect_args": {"check_same_thread": False}
    }


def test_non_sqlite_engine_does_not_receive_sqlite_connect_args():
    assert _engine_kwargs("postgresql+psycopg://user:pass@db.example/app") == {}
