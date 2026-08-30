"""turn_metrics

Revision ID: 3de09a4cc4b3
Revises: 26c5c494db77
Create Date: 2026-08-30 23:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3de09a4cc4b3"
down_revision: str | Sequence[str] | None = "26c5c494db77"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "turn_metrics",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("assistant_id", sa.UUID(), nullable=False),
        # No foreign key yet - Call (build-plan item 26) doesn't exist.
        # Adding the constraint once it does is a purely additive migration.
        sa.Column("call_id", sa.UUID(), nullable=False),
        sa.Column("stt_finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieval_done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_first_token_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_complete_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tts_first_byte_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audio_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assistant_id"], ["assistants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_turn_metrics_organization_id"),
        "turn_metrics",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_turn_metrics_workspace_id"),
        "turn_metrics",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_turn_metrics_assistant_id"),
        "turn_metrics",
        ["assistant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_turn_metrics_call_id"), "turn_metrics", ["call_id"], unique=False
    )
    op.create_index(
        op.f("ix_turn_metrics_created_at"), "turn_metrics", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_turn_metrics_created_at"), table_name="turn_metrics")
    op.drop_index(op.f("ix_turn_metrics_call_id"), table_name="turn_metrics")
    op.drop_index(op.f("ix_turn_metrics_assistant_id"), table_name="turn_metrics")
    op.drop_index(op.f("ix_turn_metrics_workspace_id"), table_name="turn_metrics")
    op.drop_index(op.f("ix_turn_metrics_organization_id"), table_name="turn_metrics")
    op.drop_table("turn_metrics")
