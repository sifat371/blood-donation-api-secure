"""P3.1 migration contract tests for durable notification delivery."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _column_map(inspector, table: str) -> dict[str, dict]:
    return {column["name"]: column for column in inspector.get_columns(table)}


def _unique_column_sets(inspector, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(table)
        if item.get("column_names")
    }


def test_notification_outbox_revision_preserves_legacy_rows(tmp_path: Path):
    db = tmp_path / "p3-notifications.db"
    url = f"sqlite:///{db}"
    command.upgrade(_config(url), "0002_multi_donor")

    engine = create_engine(url)
    now = "2026-09-17 00:00:00"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, name, email, email_verified, auth_provider, is_available, created_at, updated_at) "
                "VALUES (:id, :name, :email, 1, 'google', 1, :created, :updated)"
            ),
            [
                {
                    "id": 1,
                    "name": "Legacy User One",
                    "email": "legacy-one@example.com",
                    "created": now,
                    "updated": now,
                },
                {
                    "id": 2,
                    "name": "Legacy User Two",
                    "email": "legacy-two@example.com",
                    "created": now,
                    "updated": now,
                },
            ],
        )
        conn.execute(
            text(
                "INSERT INTO fcm_tokens (id, user_id, token, device_info, created_at) "
                "VALUES (:id, :user_id, :token, 'android', :created_at)"
            ),
            [
                {"id": 11, "user_id": 1, "token": "legacy-token-a", "created_at": now},
                {"id": 12, "user_id": 2, "token": "legacy-token-b", "created_at": now},
            ],
        )
        conn.execute(
            text(
                "INSERT INTO notifications "
                "(id, user_id, type, title, body, data, is_read, created_at) "
                "VALUES (:id, :user_id, 'New Blood Request', :title, 'body', NULL, 0, :created_at)"
            ),
            [
                {"id": 21, "user_id": 1, "title": "Legacy notification A", "created_at": now},
                {"id": 22, "user_id": 2, "title": "Legacy notification B", "created_at": now},
            ],
        )

    command.upgrade(_config(url), "head")

    inspector = inspect(engine)
    assert {"outbox_events", "notification_deliveries"} <= set(
        inspector.get_table_names()
    )

    notification_columns = _column_map(inspector, "notifications")
    assert {"event_id", "dedupe_key"} <= set(notification_columns)

    token_columns = _column_map(inspector, "fcm_tokens")
    assert {
        "device_id",
        "is_active",
        "last_seen_at",
        "updated_at",
        "disabled_at",
        "last_failure_reason",
    } <= set(token_columns)
    assert token_columns["token"]["nullable"] is True

    token_uniques = _unique_column_sets(inspector, "fcm_tokens")
    delivery_uniques = _unique_column_sets(inspector, "notification_deliveries")
    assert ("user_id", "device_id") in token_uniques
    assert ("notification_id", "fcm_token_id") in delivery_uniques

    with engine.connect() as conn:
        revision = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        preserved_tokens = list(
            conn.execute(text("SELECT token FROM fcm_tokens ORDER BY id")).scalars()
        )
        legacy_device_ids = list(
            conn.execute(text("SELECT device_id FROM fcm_tokens ORDER BY id")).scalars()
        )
        active_flags = list(
            conn.execute(text("SELECT is_active FROM fcm_tokens ORDER BY id")).scalars()
        )
        event_ids = list(
            conn.execute(text("SELECT event_id FROM notifications ORDER BY id")).scalars()
        )

    assert revision == "0003_notification_outbox"
    assert preserved_tokens == ["legacy-token-a", "legacy-token-b"]
    assert legacy_device_ids == ["legacy-11", "legacy-12"]
    assert all(bool(value) for value in active_flags)
    assert all(event_ids)
    assert len(set(event_ids)) == len(event_ids)
