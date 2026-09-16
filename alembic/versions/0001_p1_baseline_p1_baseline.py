"""P1 baseline

Revision ID: 0001_p1_baseline
Revises:
Create Date: 2026-09-16 16:20:24.375388

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "0001_p1_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("google_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("hashed_password", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(), nullable=True),
        sa.Column("auth_provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("profile_photo", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("phone", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("blood_group", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("division", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("district", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("upazila", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("last_location_updated", sa.DateTime(), nullable=True),
        sa.Column("last_donation_date", sa.Date(), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False),
        sa.Column("gender", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)
        batch_op.create_index(batch_op.f("ix_users_google_id"), ["google_id"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entity", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("entity_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "blood_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recipient_id", sa.Integer(), nullable=False),
        sa.Column("patient_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("blood_group", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("hospital_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("hospital_address", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("needed_date", sa.Date(), nullable=False),
        sa.Column("contact_number", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["accepted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("blood_requests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_blood_requests_recipient_id"), ["recipient_id"], unique=False)

    op.create_table(
        "conversation_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("tool_payload", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("conversation_history", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_conversation_history_user_id"), ["user_id"], unique=False)

    op.create_table(
        "email_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("code_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_verifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_email_verifications_code_hash"), ["code_hash"], unique=False)
        batch_op.create_index(batch_op.f("ix_email_verifications_email"), ["email"], unique=False)
        batch_op.create_index(batch_op.f("ix_email_verifications_user_id"), ["user_id"], unique=False)

    op.create_table(
        "fcm_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("device_info", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("fcm_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_fcm_tokens_token"), ["token"], unique=True)
        batch_op.create_index(batch_op.f("ix_fcm_tokens_user_id"), ["user_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("title", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("data", sa.Text(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notifications_user_id"), ["user_id"], unique=False)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_refresh_tokens_token_hash"), ["token_hash"], unique=True)
        batch_op.create_index(batch_op.f("ix_refresh_tokens_user_id"), ["user_id"], unique=False)

    op.create_table(
        "user_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_locations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_locations_user_id"), ["user_id"], unique=False)

    op.create_table(
        "donation_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("donor_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("recipient", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("hospital", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("blood_group", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["donor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["request_id"], ["blood_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("donation_history", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_donation_history_donor_id"), ["donor_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("donation_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_donation_history_donor_id"))
    op.drop_table("donation_history")

    with op.batch_alter_table("user_locations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_locations_user_id"))
    op.drop_table("user_locations")

    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_token_hash"))
    op.drop_table("refresh_tokens")

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notifications_user_id"))
    op.drop_table("notifications")

    with op.batch_alter_table("fcm_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_fcm_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_fcm_tokens_token"))
    op.drop_table("fcm_tokens")

    with op.batch_alter_table("email_verifications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_email_verifications_user_id"))
        batch_op.drop_index(batch_op.f("ix_email_verifications_email"))
        batch_op.drop_index(batch_op.f("ix_email_verifications_code_hash"))
    op.drop_table("email_verifications")

    with op.batch_alter_table("conversation_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_conversation_history_user_id"))
    op.drop_table("conversation_history")

    with op.batch_alter_table("blood_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_blood_requests_recipient_id"))
    op.drop_table("blood_requests")
    op.drop_table("audit_logs")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_google_id"))
        batch_op.drop_index(batch_op.f("ix_users_email"))
    op.drop_table("users")
