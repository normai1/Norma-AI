"""backfill settings defaults

Revision ID: 93adf6abe167
Revises: 54fee37d66e7
Create Date: 2026-08-28 09:15:10.041261

Data-only migration: no column, type, or constraint changes. Stamps the
validated default settings shape (feature 8b) onto every row still sitting
at the raw '{}' the DB-level default used to allow. Only rows that were
still exactly '{}' are touched, so a row a user has already customized is
never overwritten.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '93adf6abe167'
down_revision: str | Sequence[str] | None = '54fee37d66e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORGANIZATION_DEFAULT_SETTINGS = '{"currency": "USD"}'
WORKSPACE_DEFAULT_SETTINGS = (
    '{"timezone": "UTC", "locale": "en-US", "business_hours": null}'
)


def upgrade() -> None:
    """Backfill empty settings with the validated defaults."""

    op.execute(
        "UPDATE organizations SET settings = "
        f"'{ORGANIZATION_DEFAULT_SETTINGS}'::jsonb "
        "WHERE settings = '{}'::jsonb",
    )
    op.execute(
        "UPDATE workspaces SET settings = "
        f"'{WORKSPACE_DEFAULT_SETTINGS}'::jsonb "
        "WHERE settings = '{}'::jsonb",
    )


def downgrade() -> None:
    """Revert rows still exactly at the stamped default back to '{}'."""

    op.execute(
        "UPDATE organizations SET settings = '{}'::jsonb "
        f"WHERE settings = '{ORGANIZATION_DEFAULT_SETTINGS}'::jsonb",
    )
    op.execute(
        "UPDATE workspaces SET settings = '{}'::jsonb "
        f"WHERE settings = '{WORKSPACE_DEFAULT_SETTINGS}'::jsonb",
    )
