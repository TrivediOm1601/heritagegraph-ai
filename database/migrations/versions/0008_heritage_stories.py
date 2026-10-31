"""Stories & Legends — sourced narratives, traditions and their sources.

Revision ID: 0008_heritage_stories
Revises: 0007_india_heritage
Create Date: 2026-10-31

Additive only: ``heritage_stories`` (one narrative for one curated site),
``heritage_story_versions`` (separate traditions of the same story, never
merged) and ``heritage_story_sources`` (traceable references, optionally
attached to one version). Existing map, archive and account tables are
untouched.

Community submissions reuse ``heritage_stories`` with ``status='pending'``;
public reads only ever select ``status='published'``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_heritage_stories"
down_revision: Union[str, None] = "0007_india_heritage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "heritage_stories",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("site_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("narrative_type", sa.String(32), nullable=False),
        sa.Column("tradition", sa.String(200), nullable=True),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("community", sa.String(200), nullable=True),
        sa.Column("period", sa.String(120), nullable=True),
        sa.Column("cultural_background", sa.Text(), nullable=True),
        sa.Column(
            "verification_status", sa.String(24), nullable=False, server_default="unverified"
        ),
        sa.Column(
            "provenance", sa.String(120), nullable=False, server_default="curated-demo-india"
        ),
        sa.Column(
            "dataset", sa.String(64), nullable=False, server_default="curated-demo-india"
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("contributor_display", sa.String(120), nullable=True),
        sa.Column("interview_date", sa.String(40), nullable=True),
        sa.Column("consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["site_id"], ["heritage_sites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contributor_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "category IN ('history', 'mythology', 'religious-tradition', 'local-legend', "
            "'architecture', 'culture', 'lesser-known', 'oral-history')",
            name="ck_heritage_stories_category",
        ),
        sa.CheckConstraint(
            "narrative_type IN ('historically-documented', 'archaeological-support', "
            "'religious-text', 'religious-tradition', 'mythological-narrative', "
            "'local-folklore', 'oral-history', 'scholarly-interpretation', "
            "'unverified-account', 'conflicting-accounts', 'community-account')",
            name="ck_heritage_stories_narrative_type",
        ),
        sa.CheckConstraint(
            "status IN ('published', 'pending', 'rejected')",
            name="ck_heritage_stories_status",
        ),
        sa.CheckConstraint(
            "verification_status IN ('source-checked', 'unverified', 'pending-review', "
            "'community-approved')",
            name="ck_heritage_stories_verification_status",
        ),
    )
    op.create_index("ix_heritage_stories_site", "heritage_stories", ["site_id"])
    op.create_index("ix_heritage_stories_category", "heritage_stories", ["category"])
    op.create_index("ix_heritage_stories_status", "heritage_stories", ["status"])

    op.create_table(
        "heritage_story_versions",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("story_id", sa.String(96), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("tradition", sa.String(200), nullable=True),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("community", sa.String(200), nullable=True),
        sa.Column("differences", sa.Text(), nullable=True),
        sa.Column(
            "verification_status", sa.String(24), nullable=False, server_default="unverified"
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["story_id"], ["heritage_stories.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "verification_status IN ('source-checked', 'unverified', 'pending-review', "
            "'community-approved')",
            name="ck_heritage_story_versions_verification_status",
        ),
    )
    op.create_index(
        "ix_heritage_story_versions_story", "heritage_story_versions", ["story_id"]
    )

    op.create_table(
        "heritage_story_sources",
        sa.Column("id", sa.String(96), primary_key=True),
        sa.Column("story_id", sa.String(96), nullable=False),
        sa.Column("version_id", sa.String(96), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("author", sa.String(300), nullable=True),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("published", sa.String(40), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("locator", sa.String(200), nullable=True),
        sa.Column("licence", sa.String(120), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["story_id"], ["heritage_stories.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["heritage_story_versions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "source_type IN ('historical-record', 'religious-text', "
            "'archaeological-publication', 'government-publication', "
            "'academic-research', 'oral-history-interview', 'community-contribution', "
            "'secondary-reference', 'world-heritage-listing')",
            name="ck_heritage_story_sources_source_type",
        ),
    )
    op.create_index("ix_heritage_story_sources_story", "heritage_story_sources", ["story_id"])
    op.create_index(
        "ix_heritage_story_sources_version", "heritage_story_sources", ["version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_heritage_story_sources_version", table_name="heritage_story_sources")
    op.drop_index("ix_heritage_story_sources_story", table_name="heritage_story_sources")
    op.drop_table("heritage_story_sources")
    op.drop_index("ix_heritage_story_versions_story", table_name="heritage_story_versions")
    op.drop_table("heritage_story_versions")
    op.drop_index("ix_heritage_stories_status", table_name="heritage_stories")
    op.drop_index("ix_heritage_stories_category", table_name="heritage_stories")
    op.drop_index("ix_heritage_stories_site", table_name="heritage_stories")
    op.drop_table("heritage_stories")
