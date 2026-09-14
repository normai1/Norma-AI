"""hnsw iterative scan for tenant-filtered retrieval

Retrieval filters by organization, workspace and assistant - mandatory, and
CLAUDE.md section 6.3 will not have it otherwise - but HNSW walks its index in
global nearest-first order and the filter is applied afterwards. Every vector
belonging to another tenant that happens to sit near the query is fetched,
checked and thrown away. Measured with 453 of 10,594 chunks belonging
elsewhere: 983ms for one top-5 lookup even with the index in place, against a
per-turn retrieval budget of 80ms. It gets worse as tenants are added, not
better: the index is shared, and each new tenant is more noise in everyone
else's search.

pgvector 0.8's iterative scan resumes the search until enough rows survive the
filter rather than returning too few. Same query, 17ms.

strict_order rather than relaxed_order. They measured the same here - 16.5ms
against 17.2ms - and retrieval uses the distances directly: they become the
scores a relevance floor is applied to and that observability reports. True
distance order is worth more than a millisecond.

Set on the database rather than per query, because pgvector registers this
parameter only when its library is first loaded into a backend. A SET issued
on a connection that has not yet run a vector query raises "unrecognized
configuration parameter" and fails the turn. As a database-level setting it is
stored as a placeholder and applied once the extension loads.

Revision ID: 383253cf1d7c
Revises: c8d8004f5ca3
Create Date: 2026-09-14 20:14:24.749861

"""

from collections.abc import Sequence

from alembic import op

revision: str = "383253cf1d7c"
down_revision: str | Sequence[str] | None = "c8d8004f5ca3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _database_name() -> str:
    """
    The database this migration is running against.

    ALTER DATABASE takes no expression, so the name has to be interpolated -
    and it comes from the connection rather than from configuration so a
    migration run against a test or review database changes that one and not
    whatever the settings happen to name.
    """

    return op.get_bind().exec_driver_sql("SELECT current_database()").scalar_one()


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f'ALTER DATABASE "{_database_name()}" '
        "SET hnsw.iterative_scan = 'strict_order'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(f'ALTER DATABASE "{_database_name()}" RESET hnsw.iterative_scan')
