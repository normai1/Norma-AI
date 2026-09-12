"""turn metric correlation and cost

Item 25b. Four additive, nullable columns on turn_metrics: the turn's own
correlation identifier (the one stamped on that turn's log lines) and the
tokens and cost its LLM call reported.

Additive and backwards-compatible within a deploy, as CLAUDE.md section 6.2
requires: apps/api and apps/voice ship separately and will briefly run
different code against this schema. An older voice worker simply sends none
of these and writes the same valid latency row it always did; a newer one
against an older API has its extra fields ignored by that API's request
model. Nothing here rewrites or backfills existing rows - a metric recorded
before this migration genuinely has no turn id and no known cost.

Revision ID: 9f54b26fb40a
Revises: e57b0d4a91c8
Create Date: 2026-09-12 11:55:16.100829

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9f54b26fb40a'
down_revision: str | Sequence[str] | None = 'e57b0d4a91c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('turn_metrics', sa.Column('turn_id', sa.UUID(), nullable=True))
    op.add_column('turn_metrics', sa.Column('prompt_tokens', sa.Integer(), nullable=True))
    op.add_column('turn_metrics', sa.Column('completion_tokens', sa.Integer(), nullable=True))
    op.add_column('turn_metrics', sa.Column('cost_micro_usd', sa.BigInteger(), nullable=True))
    # Indexed, not unique: the point of this column is to look a turn up
    # by the identifier a log line quotes. Uniqueness would let a
    # duplicate identifier reject a turn's metrics outright, over a field
    # that is diagnostic only.
    op.create_index(
        op.f('ix_turn_metrics_turn_id'), 'turn_metrics', ['turn_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_turn_metrics_turn_id'), table_name='turn_metrics')
    op.drop_column('turn_metrics', 'cost_micro_usd')
    op.drop_column('turn_metrics', 'completion_tokens')
    op.drop_column('turn_metrics', 'prompt_tokens')
    op.drop_column('turn_metrics', 'turn_id')
