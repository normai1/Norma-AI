"""manual-FAQ containers are completed, not pending

Revision ID: e57b0d4a91c8
Revises: c93f1a7b2e64
Create Date: 2026-09-08

A manual_faq knowledge source is a container, not a job: there is nothing to
parse, crawl or embed at the container level, so it is usable the moment it
exists. It was nonetheless left at the 'pending' default, on the reasoning
that there was no operation to transition it away from.

That was harmless while the UI printed the raw status word. It stopped being
harmless once the operator was shown what the status *means*: every FAQ
container in the system - including the auto-generated one holding the
questions a website crawl had just produced - reported itself as a knowledge
base still being built, indefinitely.

Data-only, and narrow: only manual_faq rows, and only those still at
'pending'. A file or website source's status is a real record of a real
operation and is not touched. A manual_faq row that somehow carries 'failed'
is left alone too, rather than being quietly marked healthy.
"""

from alembic import op

revision = "e57b0d4a91c8"
down_revision = "c93f1a7b2e64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE knowledge_sources
           SET status = 'completed'
         WHERE type = 'manual_faq'
           AND status = 'pending'
        """
    )


def downgrade() -> None:
    # Deliberately not reversed: 'pending' was never a meaningful state for
    # these rows, and putting it back would only restore the bug. The column
    # itself is unchanged, so nothing structural depends on this.
    pass
