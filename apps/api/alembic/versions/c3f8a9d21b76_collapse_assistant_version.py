"""collapse assistant version

Revision ID: c3f8a9d21b76
Revises: 9b7a1e3c5f42
Create Date: 2026-09-02 09:00:00.000000

Removes AssistantVersion/versioning entirely - a product decision that an
Assistant is now a single mutable row, not an immutable-snapshot pointer.
Every config field AssistantVersion held moves directly onto `assistants`.
Existing data is preserved: each assistant's currently-published version's
values are copied onto its own row before the version machinery is dropped,
so no live assistant's configuration is lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a9d21b76"
down_revision: str | Sequence[str] | None = "9b7a1e3c5f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "assistants", sa.Column("voice_id", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "assistants", sa.Column("language", sa.String(length=32), nullable=True)
    )
    op.add_column("assistants", sa.Column("greeting", sa.Text(), nullable=True))
    op.add_column("assistants", sa.Column("persona", sa.Text(), nullable=True))
    op.add_column("assistants", sa.Column("custom_prompt", sa.Text(), nullable=True))
    op.add_column(
        "assistants",
        sa.Column(
            "speech_rate", sa.Numeric(3, 2), nullable=False, server_default="1.0"
        ),
    )
    op.add_column(
        "assistants",
        sa.Column(
            "turn_sensitivity", sa.Numeric(3, 2), nullable=False, server_default="0.5"
        ),
    )
    op.add_column(
        "assistants",
        sa.Column("creativity", sa.Numeric(3, 2), nullable=False, server_default="0.3"),
    )
    op.add_column(
        "assistants", sa.Column("ambient_sound", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "assistants", sa.Column("ambient_sound_volume", sa.Numeric(3, 2), nullable=True)
    )
    op.add_column(
        "assistants",
        sa.Column("max_call_duration_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assistants",
        sa.Column("max_silence_timeout_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assistants",
        sa.Column("record_calls", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "assistants",
        sa.Column(
            "auto_delete_on_declined_consent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Preserve every currently-published assistant's real configuration
    # before the version it points at is gone for good.
    op.execute(
        """
        UPDATE assistants a
        SET voice_id = v.voice_id,
            language = v.language,
            greeting = v.greeting,
            persona = v.persona,
            custom_prompt = v.custom_prompt,
            speech_rate = v.speech_rate,
            turn_sensitivity = v.turn_sensitivity,
            creativity = v.creativity,
            ambient_sound = v.ambient_sound,
            ambient_sound_volume = v.ambient_sound_volume,
            max_call_duration_seconds = v.max_call_duration_seconds,
            max_silence_timeout_seconds = v.max_silence_timeout_seconds,
            record_calls = v.record_calls,
            auto_delete_on_declined_consent = v.auto_delete_on_declined_consent
        FROM assistant_versions v
        WHERE a.current_version_id = v.id
        """
    )

    op.drop_constraint(
        "fk_assistants_current_version_id", "assistants", type_="foreignkey"
    )
    op.drop_column("assistants", "current_version_id")

    op.drop_table("assistant_versions")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        "assistant_versions",
        sa.Column("assistant_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("voice_id", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("greeting", sa.Text(), nullable=False),
        sa.Column("persona", sa.Text(), nullable=True),
        sa.Column("custom_prompt", sa.Text(), nullable=True),
        sa.Column("speech_rate", sa.Numeric(3, 2), nullable=False),
        sa.Column("turn_sensitivity", sa.Numeric(3, 2), nullable=False),
        sa.Column("creativity", sa.Numeric(3, 2), nullable=False),
        sa.Column("ambient_sound", sa.String(length=255), nullable=True),
        sa.Column("ambient_sound_volume", sa.Numeric(3, 2), nullable=True),
        sa.Column("max_call_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("max_silence_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("record_calls", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "auto_delete_on_declined_consent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
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
            ["assistant_id"], ["assistants.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assistant_id", "version", name="uq_assistant_versions_assistant_version"
        ),
    )
    op.create_index(
        op.f("ix_assistant_versions_assistant_id"),
        "assistant_versions",
        ["assistant_id"],
        unique=False,
    )

    op.add_column(
        "assistants", sa.Column("current_version_id", sa.UUID(), nullable=True)
    )

    # Recreate one version (number 1) per assistant that actually had
    # configuration, and point current_version_id back at it.
    op.execute(
        """
        INSERT INTO assistant_versions (
            assistant_id, version, voice_id, language, greeting, persona,
            custom_prompt, speech_rate, turn_sensitivity, creativity,
            ambient_sound, ambient_sound_volume, max_call_duration_seconds,
            max_silence_timeout_seconds, record_calls,
            auto_delete_on_declined_consent
        )
        SELECT id, 1, voice_id, language, greeting, persona, custom_prompt,
               speech_rate, turn_sensitivity, creativity, ambient_sound,
               ambient_sound_volume, max_call_duration_seconds,
               max_silence_timeout_seconds, record_calls,
               auto_delete_on_declined_consent
        FROM assistants
        WHERE voice_id IS NOT NULL AND greeting IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE assistants a
        SET current_version_id = v.id
        FROM assistant_versions v
        WHERE v.assistant_id = a.id AND v.version = 1
        """
    )

    op.create_foreign_key(
        "fk_assistants_current_version_id",
        "assistants",
        "assistant_versions",
        ["current_version_id"],
        ["id"],
    )

    op.drop_column("assistants", "auto_delete_on_declined_consent")
    op.drop_column("assistants", "record_calls")
    op.drop_column("assistants", "max_silence_timeout_seconds")
    op.drop_column("assistants", "max_call_duration_seconds")
    op.drop_column("assistants", "ambient_sound_volume")
    op.drop_column("assistants", "ambient_sound")
    op.drop_column("assistants", "creativity")
    op.drop_column("assistants", "turn_sensitivity")
    op.drop_column("assistants", "speech_rate")
    op.drop_column("assistants", "custom_prompt")
    op.drop_column("assistants", "persona")
    op.drop_column("assistants", "greeting")
    op.drop_column("assistants", "language")
    op.drop_column("assistants", "voice_id")
