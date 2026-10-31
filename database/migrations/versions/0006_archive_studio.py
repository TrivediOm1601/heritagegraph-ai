"""Heritage Archive Studio — archives, extracted text, timeline, research, map.

Revision ID: 0006_archive_studio
Revises: 0005_users_sessions_history
Create Date: 2026-10-30

Adds the platform layer on top of the existing analysis schema:

* ``archives`` / ``archive_files`` — user-owned collections and the files
  inside them. The original upload is preserved on disk (``storage_key``);
  only metadata and extracted text live in the database.
* ``archive_pages`` — page/slide/section level text, so every extracted
  passage keeps its location in the source document.
* ``archive_entities`` — links extracted candidates to the existing
  canonical ``entities`` table (no second entity system).
* ``timeline_events`` — dated passages with a *nullable* numeric range, so
  a century or a "c." date is never flattened into a false exact year.
* ``research_collections`` / ``collection_items`` / ``research_notes`` —
  the personal research workspace.
* ``heritage_sites`` — the curated, sourced map dataset (no user data).

Everything is additive; ``downgrade`` drops only what this revision created.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_archive_studio"
down_revision: Union[str, None] = "0005_users_sessions_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ID = sa.String(128)

_ARCHIVE_FILE_KINDS = ("image", "pdf", "docx", "pptx", "text", "zip")
_ARCHIVE_FILE_STATUSES = (
    "uploaded",
    "queued",
    "processing",
    "ocr_completed",
    "text_extracted",
    "analysed",
    "completed",
    "partial",
    "failed",
)
_DUPLICATE_KINDS = ("exact", "similar_image", "similar_text")
_DATE_PRECISIONS = ("exact", "approximate", "century", "range", "unknown")
_VERIFICATION_STATUSES = ("verified", "disputed", "unsupported", "unverified")


def upgrade() -> None:
    op.create_table(
        "archives",
        sa.Column("id", _ID, nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("region", sa.String(120), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_archives_user_created", "archives", ["user_id", "created_at"])

    op.create_table(
        "archive_files",
        sa.Column("id", _ID, nullable=False),
        sa.Column("archive_id", _ID, nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("parent_file_id", _ID, nullable=True),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("perceptual_hash", sa.String(32), nullable=True),
        sa.Column("text_fingerprint", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="uploaded"),
        sa.Column("status_detail", sa.String(255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column("duplicate_of_id", _ID, nullable=True),
        sa.Column("duplicate_kind", sa.String(16), nullable=True),
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
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_file_id"], ["archive_files.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_id"], ["archive_files.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            f"kind IN {_ARCHIVE_FILE_KINDS!r}", name="ck_archive_files_kind"
        ),
        sa.CheckConstraint(
            f"status IN {_ARCHIVE_FILE_STATUSES!r}", name="ck_archive_files_status"
        ),
        sa.CheckConstraint(
            f"duplicate_kind IS NULL OR duplicate_kind IN {_DUPLICATE_KINDS!r}",
            name="ck_archive_files_duplicate_kind",
        ),
    )
    op.create_index(
        "ix_archive_files_archive", "archive_files", ["archive_id", "created_at"]
    )
    op.create_index("ix_archive_files_user", "archive_files", ["user_id"])
    op.create_index("ix_archive_files_sha256", "archive_files", ["sha256"])
    op.create_index("ix_archive_files_status", "archive_files", ["status"])
    op.create_index("ix_archive_files_parent", "archive_files", ["parent_file_id"])

    op.create_table(
        "archive_pages",
        sa.Column("id", _ID, nullable=False),
        sa.Column("file_id", _ID, nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "text_source", sa.String(24), nullable=False, server_default="extracted"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["file_id"], ["archive_files.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("file_id", "page_number", name="uq_archive_pages_file_page"),
    )
    op.create_index("ix_archive_pages_file", "archive_pages", ["file_id"])

    op.create_table(
        "archive_entities",
        sa.Column("file_id", _ID, nullable=False),
        sa.Column("entity_id", _ID, nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "extraction_method", sa.String(24), nullable=False, server_default="heuristic"
        ),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("file_id", "entity_id"),
        sa.ForeignKeyConstraint(["file_id"], ["archive_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_archive_entities_entity", "archive_entities", ["entity_id"])

    op.create_table(
        "timeline_events",
        sa.Column("id", _ID, nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("archive_id", _ID, nullable=True),
        sa.Column("file_id", _ID, nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("date_text", sa.String(64), nullable=False),
        sa.Column("year_start", sa.Integer(), nullable=True),
        sa.Column("year_end", sa.Integer(), nullable=True),
        sa.Column("era", sa.String(8), nullable=False, server_default="unknown"),
        sa.Column("precision", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("evidence_excerpt", sa.Text(), nullable=True),
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["archive_files.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            f"precision IN {_DATE_PRECISIONS!r}",
            name="ck_timeline_events_precision",
        ),
        sa.CheckConstraint(
            "era IN ('ce', 'bce', 'unknown')", name="ck_timeline_events_era"
        ),
        sa.CheckConstraint(
            f"verification_status IN {_VERIFICATION_STATUSES!r}",
            name="ck_timeline_events_verification_status",
        ),
    )
    op.create_index("ix_timeline_events_user", "timeline_events", ["user_id"])
    op.create_index("ix_timeline_events_archive", "timeline_events", ["archive_id"])
    op.create_index("ix_timeline_events_file", "timeline_events", ["file_id"])

    op.create_table(
        "research_collections",
        sa.Column("id", _ID, nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_research_collections_user", "research_collections", ["user_id", "created_at"]
    )

    op.create_table(
        "collection_items",
        sa.Column("collection_id", _ID, nullable=False),
        sa.Column("archive_id", _ID, nullable=True),
        sa.Column("file_id", _ID, nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("collection_id"),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["research_collections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["archive_id"], ["archives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["archive_files.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "(archive_id IS NULL) <> (file_id IS NULL)",
            name="ck_collection_items_one_target",
        ),
    )
    op.create_index("ix_collection_items_collection", "collection_items", ["collection_id"])

    op.create_table(
        "research_notes",
        sa.Column("id", _ID, nullable=False),
        sa.Column("user_id", _ID, nullable=False),
        sa.Column("collection_id", _ID, nullable=True),
        sa.Column("subject_query", sa.String(512), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["research_collections.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_research_notes_user", "research_notes", ["user_id", "created_at"])
    op.create_index("ix_research_notes_collection", "research_notes", ["collection_id"])

    op.create_table(
        "heritage_sites",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("region", sa.String(120), nullable=False),
        sa.Column("state", sa.String(80), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("period", sa.String(120), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("coordinate_source", sa.String(256), nullable=True),
        sa.Column(
            "coordinate_accuracy",
            sa.String(16),
            nullable=False,
            server_default="approximate",
        ),
        sa.Column(
            "verification_status",
            sa.String(16),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("image_url", sa.String(1024), nullable=True),
        sa.Column("image_credit", sa.String(256), nullable=True),
        sa.Column("image_license", sa.String(80), nullable=True),
        sa.Column(
            "dataset",
            sa.String(64),
            nullable=False,
            server_default="curated-demo",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "coordinate_accuracy IN ('verified', 'approximate')",
            name="ck_heritage_sites_coordinate_accuracy",
        ),
        sa.CheckConstraint(
            f"verification_status IN {_VERIFICATION_STATUSES!r}",
            name="ck_heritage_sites_verification_status",
        ),
    )
    op.create_index("ix_heritage_sites_region", "heritage_sites", ["region"])
    op.create_index("ix_heritage_sites_category", "heritage_sites", ["category"])


def downgrade() -> None:
    """Remove only what this revision created."""
    op.drop_index("ix_heritage_sites_category", table_name="heritage_sites")
    op.drop_index("ix_heritage_sites_region", table_name="heritage_sites")
    op.drop_table("heritage_sites")

    op.drop_index("ix_research_notes_collection", table_name="research_notes")
    op.drop_index("ix_research_notes_user", table_name="research_notes")
    op.drop_table("research_notes")

    op.drop_index("ix_collection_items_collection", table_name="collection_items")
    op.drop_table("collection_items")

    op.drop_index("ix_research_collections_user", table_name="research_collections")
    op.drop_table("research_collections")

    op.drop_index("ix_timeline_events_file", table_name="timeline_events")
    op.drop_index("ix_timeline_events_archive", table_name="timeline_events")
    op.drop_index("ix_timeline_events_user", table_name="timeline_events")
    op.drop_table("timeline_events")

    op.drop_index("ix_archive_entities_entity", table_name="archive_entities")
    op.drop_table("archive_entities")

    op.drop_index("ix_archive_pages_file", table_name="archive_pages")
    op.drop_table("archive_pages")

    op.drop_index("ix_archive_files_parent", table_name="archive_files")
    op.drop_index("ix_archive_files_status", table_name="archive_files")
    op.drop_index("ix_archive_files_sha256", table_name="archive_files")
    op.drop_index("ix_archive_files_user", table_name="archive_files")
    op.drop_index("ix_archive_files_archive", table_name="archive_files")
    op.drop_table("archive_files")

    op.drop_index("ix_archives_user_created", table_name="archives")
    op.drop_table("archives")
