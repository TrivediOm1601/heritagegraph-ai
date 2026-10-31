"""C1 — canonical database schema (SQLAlchemy 2.x declarative models).

Owner: Module C (Member 3). The schema is derived from the **frozen API
contract** (``backend/app/schemas/*``, mirrored by
``frontend/src/types/api.ts``) and the C1 required-entity list — the
controlled vocabularies are imported straight from the API schemas so the
database can never drift from what the API advertises.

Tables (C1): ``entities``, ``historical_records``, ``relationships``,
``evidence``, ``analysis_runs``, ``analysis_entities``.

Design notes
------------
* **Dialect-portable**: no PostgreSQL-only types, so the *same* Alembic
  migration reproduces the schema on PostgreSQL (deployment via
  ``DATABASE_URL``) and on SQLite (schema tests, which run in CI without
  a server).
* **Opaque ids**: every id column is ``String(128)`` — matching the API
  contract's ``max_length=128`` on ids (Module A's fixtures already use
  ids like ``rel-disputed``, so ids are opaque strings, not only UUIDs).
  Database-generated ids default to UUID4.
* **Vocabularies come from the contract**:
  ``VERIFICATION_STATUSES`` from ``schemas/graph.py``,
  ``AnalysisState`` from ``schemas/analysis.py``.
  ``ENTITY_TYPES`` is Module C's canonical entity-type vocabulary (see
  below).
* **Indexes**: every foreign key is indexed (PostgreSQL does not do this
  automatically and both repositories and cascade deletes walk these
  edges), plus one lookup index per frequently filtered natural key.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, get_args

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..schemas.analysis import AnalysisState
from ..schemas.graph import VERIFICATION_STATUSES
from ..schemas.story import (
    NARRATIVE_TYPES,
    SOURCE_TYPES,
    STORY_CATEGORIES,
    STORY_STATUSES,
    STORY_VERIFICATION_STATUSES,
)

#: Module C's canonical entity-type vocabulary. The shared contract
#: documents ``person, place, event, date`` (its descriptions end in
#: "..."), and ``other`` is a documented escape hatch so a new extraction
#: category can never be blocked by a CHECK violation mid-run. Extending
#: or tightening this list requires a migration.
ENTITY_TYPES: tuple[str, ...] = ("person", "place", "event", "date", "other")

#: Lifecycle states, imported from the frozen API contract.
ANALYSIS_STATES: tuple[str, ...] = get_args(AnalysisState)

#: History kinds (text search vs image analysis).
HISTORY_KINDS: tuple[str, ...] = ("text_search", "image_analysis")

#: Heritage Archive Studio — the kinds of material an archive can hold. The
#: value is derived from the *content* (extension + declared type + a real
#: format check), never from the file name alone.
ARCHIVE_FILE_KINDS: tuple[str, ...] = (
    "image",
    "pdf",
    "docx",
    "pptx",
    "text",
    "zip",
)

#: Per-file processing lifecycle. ``ocr_completed`` and ``text_extracted``
#: are real intermediate states (they are what the pipeline actually did),
#: not cosmetic progress labels: a scanned page that OCR read but that is
#: still being analysed is truthfully in ``ocr_completed``.
ARCHIVE_FILE_STATUSES: tuple[str, ...] = (
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

#: How a duplicate was established (never used to delete anything).
DUPLICATE_KINDS: tuple[str, ...] = ("exact", "similar_image", "similar_text")

#: Date granularity extracted from a document. Uncertain dates must stay
#: uncertain: a century is not silently turned into a year.
DATE_PRECISIONS: tuple[str, ...] = (
    "exact",
    "approximate",
    "century",
    "range",
    "unknown",
)

#: Verification statuses, imported from the frozen API contract. The
#: database default is ``unverified`` — an absent status must never be
#: presented as verified.
assert set(VERIFICATION_STATUSES) == {"verified", "disputed", "unsupported", "unverified"}

_ID = String(128)
_NAME = String(512)


def _uuid_default() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for Module C's models."""


class User(Base):
    """One registered account.

    Passwords are stored **only** as a salted PBKDF2-HMAC-SHA256 hash (see
    :mod:`app.services.auth_service`); no plaintext or reversible form is
    ever persisted, logged or returned. Emails are stored normalised
    (stripped + lowercased) so uniqueness is case-insensitive.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    #: Normalised (lowercased) login address. Uniqueness is enforced by
    #: the unique index below (a column-level ``unique=True`` would add a
    #: second, redundant constraint and diverge from the migration).
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: ``pbkdf2_sha256$<iterations>$<salt>$<digest>`` — never returned by the API.
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        Index("ix_users_email", "email", unique=True),
    )


class UserSession(Base):
    """One server-side login session.

    The primary key is the **SHA-256 hash** of the session token, not the
    token itself: a database leak cannot be replayed as a live session.
    Expiry is enforced server-side on every request, and logout deletes
    the row (immediate invalidation, no client-side trust).
    """

    __tablename__ = "user_sessions"

    #: SHA-256 hex digest of the cookie token.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Coarse client label for the session list (never used for auth).
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )


class AnalysisRun(Base):
    """One submitted analysis (row appears when an upload is accepted)."""

    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    #: Owning account. Nullable for rows created before authentication
    #: existed (they are not exposed to any user) and for anonymous
    #: deployments that explicitly allow unauthenticated analysis.
    user_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: queued -> processing -> completed/failed (contract: AnalysisState)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    #: 0..1 when reported (contract: progress)
    progress: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    #: OCR output (contract: extractedText)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Repair R4 — the vision model's reading (contract: interpretation);
    #: AI-generated candidate reading, stored verbatim for the read side.
    interpretation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: grounded historical briefing assembled from retrieved sources
    #: (contract: briefing). Text quoted from those sources with citation
    #: indexes — stored as JSON so the read side returns it unchanged
    #: rather than re-fetching sources on every poll.
    briefing: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    #: non-fatal warnings / uncertainty notes (contract: warnings)
    warnings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    #: failure detail when status == 'failed' (contract: error)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: upload metadata (no image bytes are stored in the database)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN {ANALYSIS_STATES!r}",
            name="ck_analysis_runs_status",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 1)",
            name="ck_analysis_runs_progress",
        ),
        # status is the hot filter ("what is queued/failed?").
        Index("ix_analysis_runs_status", "status"),
        # recency listing / cleanup sweeps.
        Index("ix_analysis_runs_created_at", "created_at"),
        # "this user's analyses" — the history/authorization hot path.
        Index("ix_analysis_runs_user_id", "user_id"),
    )


class Entity(Base):
    """Canonical (deduplicated) extracted entity.

    Global to the system: the same person/place/event mentioned by two
    analyses resolves to one row via the ``(name, entity_type)``
    uniqueness rule; ``analysis_entities`` links runs to entities.
    """

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    #: entity text as extracted (contract: AnalysisEntity.name)
    name: Mapped[str] = mapped_column(_NAME, nullable=False)
    #: canonical category (contract: AnalysisEntity.type) — see ENTITY_TYPES
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            f"entity_type IN {ENTITY_TYPES!r}",
            name="ck_entities_entity_type",
        ),
        # Deduplicate the canonical store: same text + same type = one entity.
        UniqueConstraint("name", "entity_type", name="uq_entities_name_type"),
        # "all persons" / "all places" browses and type-scoped matching.
        Index("ix_entities_entity_type", "entity_type"),
        # Lookup-by-name during entity normalization.
        Index("ix_entities_name", "name"),
    )


class HistoricalRecord(Base):
    """A curated, source-backed historical record (Module C's curation)."""

    __tablename__ = "historical_records"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    #: display title (contract: HistoricalMatch.title)
    title: Mapped[str] = mapped_column(_NAME, nullable=False)
    #: free-text year, e.g. "c. 1843" (contract: HistoricalMatch.year)
    year: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: where the record comes from — NOT NULL because records are
    #: source-backed by charter (contract: HistoricalMatch.source)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    #: canonical link to the cited source material — the traceability the
    #: charter requires (contract: HistoricalMatch.url / EvidenceItem.url).
    #: Nullable for records that genuinely have no URL; never invented.
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    #: record body used by retrieval/matching; metadata-only records may
    #: omit it
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        # Same title from the same source is the same record.
        UniqueConstraint("title", "source", name="uq_historical_records_title_source"),
        # Source-scoped browsing ("everything from archive X").
        Index("ix_historical_records_source", "source"),
    )


class Relationship(Base):
    """One edge of one analysis' knowledge graph."""

    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    analysis_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("entities.id", ondelete="RESTRICT"), nullable=False
    )
    target_entity_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("entities.id", ondelete="RESTRICT"), nullable=False
    )
    #: relationship description shown on the edge (contract: GraphEdge.label)
    label: Mapped[Optional[str]] = mapped_column(_NAME, nullable=True)
    #: backend's actual verification state (contract: GraphEdge
    #: verificationStatus). Defaults to 'unverified' — never 'verified'.
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified", server_default="unverified"
    )
    #: relationship-level evidence summary/notes (contract:
    #: EvidenceResponse.summary / .notes)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            f"verification_status IN {tuple(VERIFICATION_STATUSES)!r}",
            name="ck_relationships_verification_status",
        ),
        # A node cannot relate to itself — always an extraction artefact.
        CheckConstraint(
            "source_entity_id <> target_entity_id", name="ck_relationships_no_self_loop"
        ),
        # One edge per entity pair per analysis (graph edges are unique
        # per pair; its leading column also serves analysis_id lookups).
        UniqueConstraint(
            "analysis_id",
            "source_entity_id",
            "target_entity_id",
            name="uq_relationships_pair",
        ),
        # FK reverse lookups ("edges of this entity") — PostgreSQL does
        # not index foreign keys automatically.
        Index("ix_relationships_source_entity", "source_entity_id"),
        Index("ix_relationships_target_entity", "target_entity_id"),
    )


class Evidence(Base):
    """One piece of supporting evidence for one relationship."""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    relationship_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("relationships.id", ondelete="CASCADE"), nullable=False
    )
    #: where the evidence comes from (contract: EvidenceItem.source)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    #: short quotation/description (contract: EvidenceItem.excerpt)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: optional link to the source material (contract: EvidenceItem.url)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )

    __table_args__ = (
        # Evidence panel loads everything for one relationship: this is
        # THE hot path of the evidence endpoint.
        Index("ix_evidence_relationship_id", "relationship_id"),
    )


class HistoryEntry(Base):
    """One row of a user's analysis history (text search or image analysis).

    Both activity types live in this one table so the frontend can show a
    single chronological list. An image-analysis entry references its
    :class:`AnalysisRun` (the heavy result stays there); a text-search
    entry stores a compact snapshot of what the user was shown, so
    reopening it never re-runs an expensive request.

    Ownership is enforced in SQL by every history query — a row is only
    ever read or exported with ``user_id == authenticated user``.
    """

    __tablename__ = "history_entries"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: 'text_search' | 'image_analysis'
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    #: What the user searched / the uploaded file name for image runs.
    query: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    #: Set for image analyses; the run row owns the result payload.
    analysis_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True
    )
    #: Lifecycle snapshot (contract: AnalysisState) so a history row can show
    #: processing/failed/partial without loading the run.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    #: Short, safe preview (first lines) — never credentials or internals.
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Compact stored result for reopening (text searches: matches +
    #: briefing; image runs: the linked run is the source of truth).
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            f"kind IN {HISTORY_KINDS!r}",
            name="ck_history_entries_kind",
        ),
        CheckConstraint(
            f"status IN {ANALYSIS_STATES!r}",
            name="ck_history_entries_status",
        ),
        # the chronological, per-user listing (owner filter + recency).
        Index("ix_history_entries_user_created", "user_id", "created_at"),
        Index("ix_history_entries_analysis_id", "analysis_id"),
    )


class Archive(Base):
    """One user-owned heritage archive (a collection of uploaded material).

    Deletion is soft (``deleted_at``): uploaded material and extracted text
    are the user's research record, so a delete is recoverable server-side
    and nothing is destroyed by a stray click. Every query filters on
    ``user_id`` *and* ``deleted_at IS NULL``.
    """

    __tablename__ = "archives"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    region: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    #: Free-form tags (a short list of short strings), validated by the route.
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # The archive list is always "my archives, newest first".
        Index("ix_archives_user_created", "user_id", "created_at"),
    )


class ArchiveFile(Base):
    """One stored file (or one member extracted from an uploaded ZIP).

    The **original bytes are preserved** under ``storage_key``; extracted
    text is stored separately (``archive_pages`` / ``extracted_text``) so a
    reprocess never destroys the source. Binary content is never written
    into a database text column.

    ``parent_file_id`` keeps the provenance of ZIP members: an extracted
    document always points at the archive it came from.
    """

    __tablename__ = "archive_files"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    archive_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("archives.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    parent_file_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("archive_files.id", ondelete="SET NULL"), nullable=True
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Content hash (exact-duplicate detection; also the storage key).
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Difference hash for near-identical photographs (64-bit, hex).
    perceptual_hash: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    #: Normalised-text digest for near-identical documents.
    text_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    #: ``ARCHIVE_FILE_KINDS`` — derived from real format sniffing.
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``ARCHIVE_FILE_STATUSES``.
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="uploaded", server_default="uploaded"
    )
    status_detail: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Relative storage key (``ab/abcdef…``), never an absolute path.
    storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Script-based language guess plus how it was obtained (see metadata).
    language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    #: Extractor diagnostics, embedded-image counts, OCR provider, warnings.
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    duplicate_of_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("archive_files.id", ondelete="SET NULL"), nullable=True
    )
    duplicate_kind: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            f"kind IN {ARCHIVE_FILE_KINDS!r}", name="ck_archive_files_kind"
        ),
        CheckConstraint(
            f"status IN {ARCHIVE_FILE_STATUSES!r}",
            name="ck_archive_files_status",
        ),
        CheckConstraint(
            "duplicate_kind IS NULL OR duplicate_kind IN "
            f"{DUPLICATE_KINDS!r}",
            name="ck_archive_files_duplicate_kind",
        ),
        Index("ix_archive_files_archive", "archive_id", "created_at"),
        Index("ix_archive_files_user", "user_id"),
        # Exact-duplicate and integrity lookups.
        Index("ix_archive_files_sha256", "sha256"),
        Index("ix_archive_files_status", "status"),
        Index("ix_archive_files_parent", "parent_file_id"),
    )


class ArchivePage(Base):
    """One page / slide / section of extracted text.

    Page numbers are 1-based and **source-relative**: a PDF page is its real
    page number, a PPTX slide is its slide number, and a DOCX/TXT section is
    a sequentially numbered structural block (labelled as such in
    ``ArchiveFile.extra``). Nothing is invented for formats that have no
    fixed pagination.
    """

    __tablename__ = "archive_pages"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    file_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("archive_files.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 'ocr' | 'text-layer' | 'extracted' — how this page's text was obtained.
    text_source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="extracted"
    )

    __table_args__ = (
        UniqueConstraint("file_id", "page_number", name="uq_archive_pages_file_page"),
        Index("ix_archive_pages_file", "file_id"),
    )


class ArchiveEntity(Base):
    """Join: which canonical entities one archived file mentions, and where.

    ``source_excerpt`` and ``page_number`` keep every extraction traceable to
    the passage it came from, and ``extraction_method`` distinguishes a
    heuristic candidate from a human-verified fact (nothing here is ever
    marked verified).
    """

    __tablename__ = "archive_entities"

    file_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("archive_files.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str] = mapped_column(
        String(24), nullable=False, default="heuristic"
    )
    source_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_archive_entities_entity", "entity_id"),
    )


class TimelineEvent(Base):
    """A dated event extracted from a document passage.

    An uncertain date is stored as what the source says (``date_text``) plus
    a *nullable* numeric range: a century has no single year, and a
    ``date_text`` of "c. 1020" is never silently treated as exact.
    """

    __tablename__ = "timeline_events"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    archive_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("archives.id", ondelete="CASCADE"), nullable=True
    )
    file_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("archive_files.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: The date exactly as the source wrote it ("c. 1020", "11th century").
    date_text: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Signed year, negative = BCE; NULL when the date has no single year.
    year_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    year_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    era: Mapped[str] = mapped_column(String(8), nullable=False, default="unknown")
    precision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    evidence_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified", server_default="unverified"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            f"precision IN {DATE_PRECISIONS!r}", name="ck_timeline_events_precision"
        ),
        CheckConstraint(
            "era IN ('ce', 'bce', 'unknown')", name="ck_timeline_events_era"
        ),
        CheckConstraint(
            f"verification_status IN {tuple(VERIFICATION_STATUSES)!r}",
            name="ck_timeline_events_verification_status",
        ),
        Index("ix_timeline_events_user", "user_id"),
        Index("ix_timeline_events_archive", "archive_id"),
        Index("ix_timeline_events_file", "file_id"),
    )


class ResearchCollection(Base):
    """A user's research collection (archives/files + notes + report)."""

    __tablename__ = "research_collections"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_research_collections_user", "user_id", "created_at"),
    )


class CollectionItem(Base):
    """One archive or one file inside a research collection."""

    __tablename__ = "collection_items"

    collection_id: Mapped[str] = mapped_column(
        _ID,
        ForeignKey("research_collections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    archive_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("archives.id", ondelete="CASCADE"), nullable=True
    )
    file_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("archive_files.id", ondelete="CASCADE"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "(archive_id IS NULL) <> (file_id IS NULL)",
            name="ck_collection_items_one_target",
        ),
        Index("ix_collection_items_collection", "collection_id"),
    )


class ResearchNote(Base):
    """A free-text research note, optionally inside a collection."""

    __tablename__ = "research_notes"

    id: Mapped[str] = mapped_column(_ID, primary_key=True, default=_uuid_default)
    user_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    collection_id: Mapped[Optional[str]] = mapped_column(
        _ID, ForeignKey("research_collections.id", ondelete="CASCADE"), nullable=True
    )
    #: Optional subject the note is about (a saved search term).
    subject_query: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        Index("ix_research_notes_user", "user_id", "created_at"),
        Index("ix_research_notes_collection", "collection_id"),
    )


class HeritageSite(Base):
    """A curated heritage place with sourced coordinates (map layer).

    Seeded demonstration data, labelled as curated in the API response.
    Coordinates carry their own source, accuracy and verification status; a
    site without a real coordinate is simply not plotted.
    """

    __tablename__ = "heritage_sites"

    #: Stable slug ("rani-ki-vav"), used by the API and the frontend.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Macro-region (North/South/East/West/Central/Northeast), validated
    #: against the documented state mapping when the curated seed loads.
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Coarse filter bucket (Prehistoric .. Contemporary); NULL when the
    #: source does not support a period.
    period_group: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    period: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    alternative_names: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    architectural_style: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    is_union_territory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    unesco_status: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    unesco_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    coordinate_source: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    #: 'verified' (surveyed) | 'approximate' (published coordinates).
    coordinate_accuracy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="approximate"
    )
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unverified"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    #: Multiple source references: [{"label": ..., "url": ...}].
    source_refs: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    image_credit: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    image_license: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    #: Curated demonstration data, never a user contribution.
    dataset: Mapped[str] = mapped_column(
        String(64), nullable=False, default="curated-demo", server_default="curated-demo"
    )

    __table_args__ = (
        CheckConstraint(
            "coordinate_accuracy IN ('verified', 'approximate')",
            name="ck_heritage_sites_coordinate_accuracy",
        ),
        CheckConstraint(
            f"verification_status IN {tuple(VERIFICATION_STATUSES)!r}",
            name="ck_heritage_sites_verification_status",
        ),
        Index("ix_heritage_sites_region", "region"),
        Index("ix_heritage_sites_category", "category"),
        Index("ix_heritage_sites_state", "state"),
        Index("ix_heritage_sites_period_group", "period_group"),
        Index("ix_heritage_sites_unesco", "unesco_status"),
    )


class HeritageStory(Base):
    """A sourced narrative attached to one curated heritage site.

    Curated stories (``dataset == 'curated-demo-india'``) are seeded and may
    be published directly. Community submissions use the same table with
    ``status='pending'`` and ``provenance='community-oral-history'``; only a
    moderator can move them to ``published``, and publication never upgrades
    the narrative to a historical claim.
    """

    __tablename__ = "heritage_stories"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    site_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("heritage_sites.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    narrative_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tradition: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="en", server_default="en"
    )
    #: Named community the tradition belongs to, when the source states one.
    community: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    period: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cultural_background: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unverified", server_default="unverified"
    )
    provenance: Mapped[str] = mapped_column(
        String(120), nullable=False, default="curated-demo-india"
    )
    dataset: Mapped[str] = mapped_column(
        String(64), nullable=False, default="curated-demo-india"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="published", server_default="published"
    )
    contributor_id: Mapped[Optional[str]] = mapped_column(
        String(128), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    contributor_display: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True
    )
    interview_date: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            f"category IN {STORY_CATEGORIES!r}",
            name="ck_heritage_stories_category",
        ),
        CheckConstraint(
            f"narrative_type IN {NARRATIVE_TYPES!r}",
            name="ck_heritage_stories_narrative_type",
        ),
        CheckConstraint(
            f"status IN {STORY_STATUSES!r}",
            name="ck_heritage_stories_status",
        ),
        CheckConstraint(
            f"verification_status IN {STORY_VERIFICATION_STATUSES!r}",
            name="ck_heritage_stories_verification_status",
        ),
        Index("ix_heritage_stories_site", "site_id"),
        Index("ix_heritage_stories_category", "category"),
        Index("ix_heritage_stories_status", "status"),
    )


class HeritageStoryVersion(Base):
    """One tradition's account of the same story (never merged away)."""

    __tablename__ = "heritage_story_versions"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    story_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("heritage_stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    narrative: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tradition: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(
        String(8), nullable=False, default="en", server_default="en"
    )
    community: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    differences: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unverified", server_default="unverified"
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            f"verification_status IN {STORY_VERIFICATION_STATUSES!r}",
            name="ck_heritage_story_versions_verification_status",
        ),
        Index("ix_heritage_story_versions_story", "story_id"),
    )


class HeritageStorySource(Base):
    """A traceable source for a story or for one of its versions.

    ``version_id`` is NULL for a source that supports the story as a whole;
    otherwise the row belongs to that version only. Sources are kept separate
    from the narrative so one story can carry several references without a
    JSON blob standing in for provenance.
    """

    __tablename__ = "heritage_story_sources"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    story_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("heritage_stories.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[Optional[str]] = mapped_column(
        String(96),
        ForeignKey("heritage_story_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    published: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    locator: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    licence: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            f"source_type IN {SOURCE_TYPES!r}",
            name="ck_heritage_story_sources_source_type",
        ),
        Index("ix_heritage_story_sources_story", "story_id"),
        Index("ix_heritage_story_sources_version", "version_id"),
    )


class AnalysisEntity(Base):
    """Join: which entities one analysis extracted, with per-run confidence.

    Composite primary key enforces one row per entity per analysis.
    """

    __tablename__ = "analysis_entities"

    analysis_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[str] = mapped_column(
        _ID, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )
    #: per-run extraction confidence, 0..1 (contract: AnalysisEntity.confidence)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_analysis_entities_confidence",
        ),
        # "which runs mention this entity" — the composite PK already
        # covers analysis_id, so only the reverse edge needs an index.
        Index("ix_analysis_entities_entity_id", "entity_id"),
    )
