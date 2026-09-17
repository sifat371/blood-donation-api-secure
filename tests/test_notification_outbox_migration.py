"""P3.1 notification outbox migration contract tests."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_notification_outbox_revision_preserves_legacy_notification_and_tokens(
    tmp_path: Path,
):
    db = tmp_path / "notification-outbox.db"
    url = f"sqlite:///{db}"
    command.upgrade(_config(url), "0002_multi_donor")

    engine = create_engine(url)
    now = "2026-09-17 10:00:00"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, email_verified, auth_provider, is_available, created_at, updated_at) "
                "VALUES (1, 'Legacy User', 'legacy-p31@example.com', 1, 'google', 1, :now, :now)"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO notifications "
                "(id, user_id, type, title, body, is_read, created_at) "
                "VALUES (11, 1, 'New Blood Request', 'Legacy alert', 'body', 0, :now)"
            ),
            {"now": now},
        )
        conn.execute(
            text(
                "INSERT INTO fcm_tokens (id, user_id, token, device_info, created_at) VALUES "
                "(21, 1, 'legacy-token-a', 'android', :now), "
                "(22, 1, 'legacy-token-b', 'android', :now)"
            ),
            {"now": now},
        )

    command.upgrade(_config(url), "head")

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    notification_columns = {
        column["name"] for column in inspector.get_columns("notifications")
    }
    token_columns = {column["name"] for column in inspector.get_columns("fcm_tokens")}

    assert {"outbox_events", "notification_deliveries"} <= tables
    assert {"event_id", "dedupe_key"} <= notification_columns
    assert {
        "device_id",
        "is_active",
        "last_seen_at",
        "updated_at",
        "disabled_at",
        "last_failure_reason",
    } <= token_columns

    token_column = next(
        column for column in inspector.get_columns("fcm_tokens") if column["name"] == "token"
    )
    assert token_column["nullable"] is True

    token_unique_constraints = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("fcm_tokens")
    }
    delivery_unique_constraints = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("notification_deliveries")
    }
    assert ("user_id", "device_id") in token_unique_constraints
    assert ("notification_id", "fcm_token_id") in delivery_unique_constraints

    with engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        preserved_tokens = conn.execute(
            text("SELECT token FROM fcm_tokens ORDER BY id")
        ).scalars().all()
        legacy_device_ids = conn.execute(
            text("SELECT device_id FROM fcm_tokens ORDER BY id")
        ).scalars().all()
        event_ids = conn.execute(
            text("SELECT event_id FROM notifications ORDER BY id")
        ).scalars().all()

    assert revision == "0003_notification_outbox"
    assert preserved_tokens == ["legacy-token-a", "legacy-token-b"]
    assert all(device_id.startswith("legacy-") for device_id in legacy_device_ids)
    assert len(set(event_ids)) == len(event_ids)
    assert all(event_ids)
