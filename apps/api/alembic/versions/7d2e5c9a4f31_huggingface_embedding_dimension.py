"""huggingface embedding dimension

Revision ID: 7d2e5c9a4f31
Revises: c3f8a9d21b76
Create Date: 2026-09-02 17:45:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d2e5c9a4f31"
down_revision: str | Sequence[str] | None = "c3f8a9d21b76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The configured EMBEDDING_PROVIDER changed from OpenAI (1536) to
# HuggingFace's intfloat/multilingual-e5-base (768) - two incompatible
# embedding spaces, not just two numbers. CLAUDE.md section 6.4 forbids
# "fixing" a dimension change by truncating or padding a vector, so every
# existing embedding is nulled out here rather than reinterpreted, and every
# knowledge source is reset to 'pending' so it visibly needs reprocessing
# through the new provider instead of silently serving stale-space vectors.
_OLD_DIMENSION = 1536
_NEW_DIMENSION = 768


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({_NEW_DIMENSION}) "
        f"USING NULL::vector({_NEW_DIMENSION})"
    )
    op.execute(
        "UPDATE knowledge_sources SET status = 'pending', "
        "error_message = 'Embedding provider changed; this source needs "
        "reprocessing.'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({_OLD_DIMENSION}) "
        f"USING NULL::vector({_OLD_DIMENSION})"
    )
    op.execute(
        "UPDATE knowledge_sources SET status = 'pending', "
        "error_message = 'Embedding provider changed; this source needs "
        "reprocessing.'"
    )
