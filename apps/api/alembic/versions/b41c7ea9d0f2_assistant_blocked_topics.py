"""assistant blocked topics

Additive with a server default, so the API and the voice worker can briefly
run different code against the same schema during a deploy (CLAUDE.md
section 6.2) - an assistant row written by the older code simply gets an
empty list, which means no blocking.

Revision ID: b41c7ea9d0f2
Revises: 7d2e5c9a4f31
Create Date: 2026-09-07

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b41c7ea9d0f2"
down_revision = "7d2e5c9a4f31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistants",
        sa.Column(
            "blocked_topics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("assistants", "blocked_topics")
