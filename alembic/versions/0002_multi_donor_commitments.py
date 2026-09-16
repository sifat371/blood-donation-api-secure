"""Migrate legacy single-donor requests to donor commitments.

Revision ID: 0002_multi_donor
Revises: 0001_p1_baseline
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_multi_donor"
down_revision: Union[str, Sequence[str], None] = "0001_p1_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "donation_commitments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("donor_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("committed_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["blood_requests.id"]),
        sa.ForeignKeyConstraint(["donor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", "donor_id", name="uq_commitment_request_donor"),
    )
    op.create_index(
        "ix_donation_commitments_request_id",
        "donation_commitments",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "ix_donation_commitments_donor_id",
        "donation_commitments",
        ["donor_id"],
        unique=False,
    )
    op.create_index(
        "ix_donation_commitments_status",
        "donation_commitments",
        ["status"],
        unique=False,
    )

    op.add_column(
        "blood_requests",
        sa.Column(
            "legacy_completion_incomplete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "donation_history",
        sa.Column("commitment_id", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("donation_history", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_donation_history_commitment_id",
            "donation_commitments",
            ["commitment_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_donation_history_commitment_id",
            ["commitment_id"],
            unique=True,
        )

    bind = op.get_bind()

    # Preserve every donor relationship that the P1 schema can actually prove.
    # A historical Accepted row becomes one committed unit. Historical completed
    # rows become one completed unit. Terminal rows keep the donor relationship
    # as a cancelled commitment rather than dropping provenance.
    bind.execute(
        sa.text(
            """
            INSERT INTO donation_commitments (
                request_id,
                donor_id,
                status,
                committed_at,
                completed_at,
                withdrawn_at,
                cancelled_at,
                created_at,
                updated_at
            )
            SELECT
                id,
                accepted_by,
                CASE
                    WHEN status = 'Completed' THEN 'Completed'
                    WHEN status IN ('Cancelled', 'Expired') THEN 'Cancelled'
                    ELSE 'Committed'
                END,
                COALESCE(updated_at, created_at, CURRENT_TIMESTAMP),
                CASE
                    WHEN status = 'Completed'
                    THEN COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END,
                NULL,
                CASE
                    WHEN status IN ('Cancelled', 'Expired')
                    THEN COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END,
                COALESCE(created_at, updated_at, CURRENT_TIMESTAMP),
                COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            FROM blood_requests
            WHERE accepted_by IS NOT NULL
            """
        )
    )

    # Accepted is migration input only. Runtime P2 never writes this state.
    bind.execute(
        sa.text(
            """
            UPDATE blood_requests
            SET status = CASE
                WHEN units <= 1 THEN 'Fully Committed'
                ELSE 'Partially Committed'
            END
            WHERE status = 'Accepted'
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE blood_requests
            SET legacy_completion_incomplete = :true_value
            WHERE status = 'Completed' AND units > 1
            """
        ),
        {"true_value": True},
    )

    # Donation-history linkage is safe because the new table has exactly one
    # row per (request_id, donor_id). Rows without a provable match stay NULL.
    bind.execute(
        sa.text(
            """
            UPDATE donation_history
            SET commitment_id = (
                SELECT dc.id
                FROM donation_commitments AS dc
                WHERE dc.request_id = donation_history.request_id
                  AND dc.donor_id = donation_history.donor_id
            )
            WHERE request_id IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM donation_commitments AS dc
                WHERE dc.request_id = donation_history.request_id
                  AND dc.donor_id = donation_history.donor_id
              )
            """
        )
    )

    # accepted_by has now been fully transformed. Batch mode keeps this portable
    # to SQLite, while PostgreSQL uses its native ALTER TABLE behavior.
    with op.batch_alter_table("blood_requests", schema=None) as batch_op:
        batch_op.drop_column("accepted_by")


def downgrade() -> None:
    raise RuntimeError(
        "0002_multi_donor is intentionally not losslessly downgradeable: "
        "multiple donor commitments cannot be collapsed into P1 accepted_by"
    )
