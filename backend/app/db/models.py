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
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..schemas.analysis import AnalysisState
from ..schemas.graph import VERIFICATION_STATUSES

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
