"""faq entry records the source it was generated from

Revision ID: c93f1a7b2e64
Revises: b41c7ea9d0f2
Create Date: 2026-09-07

Additive and nullable, so the API and the voice worker can run either side
of it during a deploy (CLAUDE.md section 6.2).

Existing rows keep NULL: an entry generated before this column existed can
no longer be traced back to its origin - the link was never recorded - and
guessing one would attribute FAQs to documents that may not have produced
them. NULL reads as "operator-authored", which is also how those entries
already behave today.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c93f1a7b2e64"
down_revision = "b41c7ea9d0f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "faq_entries",
        sa.Column(
            "generated_from_knowledge_source_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_faq_entries_generated_from_knowledge_source_id",
        "faq_entries",
        ["generated_from_knowledge_source_id"],
    )
    op.create_foreign_key(
        "fk_faq_entries_generated_from_knowledge_source_id",
        "faq_entries",
        "knowledge_sources",
        ["generated_from_knowledge_source_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_faq_entries_generated_from_knowledge_source_id",
        "faq_entries",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_faq_entries_generated_from_knowledge_source_id",
        table_name="faq_entries",
    )
    op.drop_column("faq_entries", "generated_from_knowledge_source_id")
