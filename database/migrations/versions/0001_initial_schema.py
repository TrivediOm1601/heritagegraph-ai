"""C1 — initial schema: the six required Module C tables.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-25

Explicit, dialect-portable DDL (PostgreSQL in deployment, SQLite in
schema tests) mirroring ``app.db.models`` — written as a frozen snapshot
rather than ``metadata.create_all`` so later model changes cannot
retroactively rewrite this migration.

Tables: analysis_runs, entities, historical_records, relationships,
evidence, analysis_entities.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ID = sa.String(128)
_NAME = sa.String(512)

# Must stay in sync with the frozen API contract (schemas/analysis.py,
# schemas/graph.py) and app.db.models.ENTITY_TYPES.
_ANALYSIS_STATES = ("queued", "processing", "completed", "failed")
_ENTITY_TYPES = ("person", "place", "event", "date", "other")
_VERIFICATION_STATUSES = ("verified", "disputed", "unsupported", "unverified")


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", _ID, nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="queued"
        ),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"status IN {_ANALYSIS_STATES!r}", name="ck_analysis_runs_status"
        ),
        sa.CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 1)",
            name="ck_analysis_runs_progress",
        ),
    )
    # C5 review fix: the model defines both indexes (status is the hot
    # filter, created_at the recency sweep) but the original revision
    # omitted them — caught by `alembic check`.
    op.create_index("ix_analysis_runs_status", "analysis_runs", ["status"])
    op.create_index(
        "ix_analysis_runs_created_at", "analysis_runs", ["created_at"]
    )

    op.create_table(
        "entities",
        sa.Column("id", _ID, nullable=False),
        sa.Column("name", _NAME, nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"entity_type IN {_ENTITY_TYPES!r}", name="ck_entities_entity_type"
        ),
        sa.UniqueConstraint("name", "entity_type", name="uq_entities_name_type"),
    )
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"])
    op.create_index("ix_entities_name", "entities", ["name"])

    op.create_table(
        "historical_records",
        sa.Column("id", _ID, nullable=False),
        sa.Column("title", _NAME, nullable=False),
        sa.Column("year", sa.String(64), nullable=True),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title", "source", name="uq_historical_records_title_source"),
    )
    op.create_index("ix_historical_records_source", "historical_records", ["source"])

    op.create_table(
        "relationships",
        sa.Column("id", _ID, nullable=False),
        sa.Column("analysis_id", _ID, nullable=False),
        sa.Column("source_entity_id", _ID, nullable=False),
        sa.Column("target_entity_id", _ID, nullable=False),
        sa.Column("label", _NAME, nullable=True),
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_entity_id"], ["entities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_entity_id"], ["entities.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            f"verification_status IN {_VERIFICATION_STATUSES!r}",
            name="ck_relationships_verification_status",
        ),
        sa.CheckConstraint(
            "source_entity_id <> target_entity_id",
            name="ck_relationships_no_self_loop",
        ),
        sa.UniqueConstraint(
            "analysis_id",
            "source_entity_id",
            "target_entity_id",
            name="uq_relationships_pair",
        ),
    )
    op.create_index("ix_relationships_source_entity", "relationships", ["source_entity_id"])
    op.create_index("ix_relationships_target_entity", "relationships", ["target_entity_id"])

    op.create_table(
        "evidence",
        sa.Column("id", _ID, nullable=False),
        sa.Column("relationship_id", _ID, nullable=False),
        sa.Column("source", sa.String(256), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["relationship_id"], ["relationships.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_evidence_relationship_id", "evidence", ["relationship_id"])

    op.create_table(
        "analysis_entities",
        sa.Column("analysis_id", _ID, nullable=False),
        sa.Column("entity_id", _ID, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("analysis_id", "entity_id"),
        sa.ForeignKeyConstraint(
            ["analysis_id"], ["analysis_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_analysis_entities_confidence",
        ),
    )
    op.create_index("ix_analysis_entities_entity_id", "analysis_entities", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_entities_entity_id", table_name="analysis_entities")
    op.drop_table("analysis_entities")

    op.drop_index("ix_evidence_relationship_id", table_name="evidence")
    op.drop_table("evidence")

    op.drop_index("ix_relationships_target_entity", table_name="relationships")
    op.drop_index("ix_relationships_source_entity", table_name="relationships")
    op.drop_table("relationships")

    op.drop_index("ix_historical_records_source", table_name="historical_records")
    op.drop_table("historical_records")

    op.drop_index("ix_entities_name", table_name="entities")
    op.drop_index("ix_entities_entity_type", table_name="entities")
    op.drop_table("entities")

    op.drop_index("ix_analysis_runs_created_at", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_status", table_name="analysis_runs")
    op.drop_table("analysis_runs")
