"""Add durable notification outbox and multi-device delivery state.

Revision ID: 0003_notification_outbox
Revises: 0002_multi_donor
"""

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "0003_notification_outbox"
down_revision: Union[str, Sequence[str], None] = "0002_multi_donor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Existing inbox rows remain authoritative. Add stable public/event ids and
    # an optional deterministic producer key for replay-safe fan-out.
    op.add_column("notifications", sa.Column("event_id", sa.String(), nullable=True))
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(), nullable=True))

    notification_ids = bind.execute(sa.text("SELECT id FROM notifications")).scalars().all()
    for notification_id in notification_ids:
        bind.execute(
            sa.text(
                "UPDATE notifications SET event_id = :event_id WHERE id = :notification_id"
            ),
            {
                "event_id": str(uuid4()),
                "notification_id": notification_id,
            },
        )

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.alter_column(
            "event_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.create_index(
            "ix_notifications_event_id",
            ["event_id"],
            unique=True,
        )
        batch_op.create_index(
            "ix_notifications_dedupe_key",
            ["dedupe_key"],
            unique=True,
        )

    # Evolve FCM tokens into stable installation records. Add nullable columns
    # first so existing SQLite/PostgreSQL data can be backfilled safely.
    op.add_column("fcm_tokens", sa.Column("device_id", sa.String(), nullable=True))
    op.add_column(
        "fcm_tokens",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("fcm_tokens", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.add_column("fcm_tokens", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.add_column("fcm_tokens", sa.Column("disabled_at", sa.DateTime(), nullable=True))
    op.add_column(
        "fcm_tokens",
        sa.Column("last_failure_reason", sa.String(), nullable=True),
    )

    legacy_tokens = bind.execute(
        sa.text("SELECT id, created_at FROM fcm_tokens ORDER BY id")
    ).all()
    for token_id, created_at in legacy_tokens:
        bind.execute(
            sa.text(
                """
                UPDATE fcm_tokens
                SET device_id = :device_id,
                    is_active = :is_active,
                    last_seen_at = :last_seen_at,
                    updated_at = :updated_at
                WHERE id = :token_id
                """
            ),
            {
                "device_id": f"legacy-{token_id}",
                "is_active": True,
                "last_seen_at": created_at,
                "updated_at": created_at,
                "token_id": token_id,
            },
        )

    with op.batch_alter_table("fcm_tokens", schema=None) as batch_op:
        batch_op.alter_column(
            "token",
            existing_type=sa.String(),
            nullable=True,
        )
        batch_op.alter_column(
            "device_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.alter_column(
            "last_seen_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_fcm_tokens_user_device",
            ["user_id", "device_id"],
        )
        batch_op.create_index(
            "ix_fcm_tokens_is_active",
            ["is_active"],
            unique=False,
        )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_outbox_events_idempotency_key",
        ),
    )
    op.create_index(
        "ix_outbox_events_status",
        "outbox_events",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_outbox_events_available_at",
        "outbox_events",
        ["available_at"],
        unique=False,
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("fcm_token_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=True),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("provider_message_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_category", sa.String(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"]),
        sa.ForeignKeyConstraint(["fcm_token_id"], ["fcm_tokens.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "notification_id",
            "fcm_token_id",
            name="uq_notification_delivery_notification_device",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_notification_id",
        "notification_deliveries",
        ["notification_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_deliveries_fcm_token_id",
        "notification_deliveries",
        ["fcm_token_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_deliveries_status",
        "notification_deliveries",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_notification_deliveries_available_at",
        "notification_deliveries",
        ["available_at"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "0003_notification_outbox is intentionally not losslessly downgradeable: "
        "multi-device installations and delivery/outbox history would be discarded"
    )
