"""simplify custom prompt

Revision ID: 9b7a1e3c5f42
Revises: 875b5df3ae33
Create Date: 2026-09-01 20:00:00.000000

Removes the PromptTemplate/PromptVersion system (shared, workspace-scoped,
versioned prompt templates - build-plan items 12/12a-c/23f) in favor of a
single free-text custom_prompt column directly on AssistantVersion, mirroring
persona's own shape exactly. A product decision to simplify the Custom
Prompt tab to "just a prompt, nothing else" - no template picker, no
versions, no publish/rollback for prompts.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b7a1e3c5f42"
down_revision: str | Sequence[str] | None = "875b5df3ae33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "assistant_versions", sa.Column("custom_prompt", sa.Text(), nullable=True)
    )

    op.drop_constraint(
        "fk_assistant_versions_prompt_template_id",
        "assistant_versions",
        type_="foreignkey",
    )
    op.drop_column("assistant_versions", "prompt_version")
    op.drop_column("assistant_versions", "prompt_template_id")

    op.drop_constraint(
        "fk_prompt_templates_current_version_id", "prompt_templates", type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_prompt_versions_prompt_template_id"), table_name="prompt_versions"
    )
    op.drop_table("prompt_versions")

    op.drop_index(
        op.f("ix_prompt_templates_workspace_id"), table_name="prompt_templates"
    )
    op.drop_index(
        op.f("ix_prompt_templates_organization_id"), table_name="prompt_templates"
    )
    op.drop_table("prompt_templates")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "prompt_templates",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("use_case", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_prompt_templates_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prompt_templates_organization_id"),
        "prompt_templates",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_templates_workspace_id"),
        "prompt_templates",
        ["workspace_id"],
        unique=False,
    )

    op.create_table(
        "prompt_versions",
        sa.Column("prompt_template_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
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
            ["prompt_template_id"], ["prompt_templates.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prompt_template_id",
            "version",
            name="uq_prompt_versions_prompt_template_version",
        ),
    )
    op.create_index(
        op.f("ix_prompt_versions_prompt_template_id"),
        "prompt_versions",
        ["prompt_template_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_prompt_templates_current_version_id",
        "prompt_templates",
        "prompt_versions",
        ["current_version_id"],
        ["id"],
        use_alter=True,
    )

    op.add_column(
        "assistant_versions", sa.Column("prompt_template_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "assistant_versions", sa.Column("prompt_version", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_assistant_versions_prompt_template_id",
        "assistant_versions",
        "prompt_templates",
        ["prompt_template_id"],
        ["id"],
    )

    op.drop_column("assistant_versions", "custom_prompt")
