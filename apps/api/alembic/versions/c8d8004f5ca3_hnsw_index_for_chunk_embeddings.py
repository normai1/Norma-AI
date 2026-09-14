"""hnsw index for chunk embeddings

Retrieval had no vector index, so every query sequentially scanned the
whole table computing a cosine distance per row. Measured on 10,141
chunks: 1,847ms for one top-5 lookup, against a per-turn retrieval budget
of 80ms (CLAUDE.md section 1).

Additive and safe for both planes: an index changes no data and no schema
the running code reads, so an older API keeps working against it
unchanged (CLAUDE.md section 6.2).

vector_cosine_ops because retrieval orders by cosine distance. An index
built for a different operator class is simply never used - silently,
with the sequential scan quietly coming back - which is why the operator
class is pinned here rather than left to a default.

Revision ID: c8d8004f5ca3
Revises: 9f54b26fb40a
Create Date: 2026-09-14 14:35:57.856301

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8d8004f5ca3'
down_revision: str | Sequence[str] | None = '9f54b26fb40a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_chunks_embedding_hnsw',
        'chunks',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_chunks_embedding_hnsw',
        table_name='chunks',
        postgresql_using='hnsw',
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
