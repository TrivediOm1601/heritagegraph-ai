"""C2 — relationship repository (graph edges + their persistence).

Signature note (pending agreement with Members 2 and 4):

* ``save_relationships(session, analysis_id, items)`` — ``items`` accept
  :class:`RelationshipInput`, plain dicts, or contract-shaped dicts
  (``source``/``target``/``verificationStatus``/``id`` from
  ``GraphEdge``), so Module D's candidate output can be persisted
  directly,
* pairs are upserted (last write wins) — duplicate candidates from a
  single extraction run collapse instead of failing,
* referential problems surface as sanitised :class:`NotFoundError` /
  :class:`ConflictError`, never raw SQL errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import VERIFICATION_STATUSES, AnalysisRun, Entity, Relationship
from ..db.session import ConflictError, NotFoundError, safe

__all__ = [
    "RelationshipInput",
    "get_relationship",
    "save_relationships",
    "list_relationships",
    "set_relationship_verification",
]


@dataclass(frozen=True)
class RelationshipInput:
    """One candidate edge to persist."""

    source_entity_id: str
    target_entity_id: str
    label: str | None = None
    verification_status: str = "unverified"
    relationship_id: str | None = None
    summary: str | None = None
    notes: tuple[str, ...] | list[str] | None = None


def coerce_relationship_input(item: RelationshipInput | Mapping) -> RelationshipInput:
    """Accept our own input type, plain dicts, or contract (GraphEdge) keys."""
    if isinstance(item, RelationshipInput):
        result = item
    elif isinstance(item, Mapping):
        def pick(*keys: str, default=None):
            for key in keys:
                if key in item:
                    return item[key]
            return default

        source = pick("source_entity_id", "source")
        target = pick("target_entity_id", "target")
        if source is None or target is None:
            raise ConflictError(
                "A relationship needs both a source and a target entity id."
            )
        result = RelationshipInput(
            source_entity_id=str(source),
            target_entity_id=str(target),
            label=pick("label"),
            verification_status=pick("verification_status", "verificationStatus")
            or "unverified",
            relationship_id=pick("relationship_id", "id"),
            summary=pick("summary"),
            notes=pick("notes"),
        )
    else:
        raise ConflictError("Unsupported relationship payload.")

    if not result.source_entity_id or not result.target_entity_id:
        raise ConflictError("A relationship needs both a source and a target entity id.")
    if result.source_entity_id == result.target_entity_id:
        raise ConflictError("A relationship cannot connect an entity to itself.")
    if result.verification_status not in VERIFICATION_STATUSES:
        raise ConflictError(
            f"Unknown verification status {result.verification_status!r}; "
            "allowed: " + ", ".join(VERIFICATION_STATUSES)
        )
    return result


@safe
async def get_relationship(
    session: AsyncSession,
    relationship_id: str,
    *,
    analysis_id: str | None = None,
) -> Relationship | None:
    """Fetch one edge by id (optionally scoped to an analysis)."""
    statement = select(Relationship).where(Relationship.id == relationship_id)
    if analysis_id is not None:
        statement = statement.where(Relationship.analysis_id == analysis_id)
    return (await session.scalars(statement)).first()


async def _require_analysis(session: AsyncSession, analysis_id: str) -> None:
    if await session.get(AnalysisRun, analysis_id) is None:
        raise NotFoundError(f"Analysis {analysis_id!r} does not exist.")


async def _require_entities(session: AsyncSession, ids: set[str]) -> None:
    if not ids:
        return
    found = set(
        (await session.scalars(select(Entity.id).where(Entity.id.in_(ids)))).all()
    )
    missing = sorted(ids - found)
    if missing:
        shown = ", ".join(missing[:10])
        suffix = " …" if len(missing) > 10 else ""
        raise NotFoundError(f"Unknown entity id(s): {shown}{suffix}")


@safe
async def list_relationships(
    session: AsyncSession,
    analysis_id: str,
) -> list[Relationship]:
    """All edges of one analysis, deterministically ordered (empty list
    when the analysis has none — an empty graph is not an error)."""
    statement = (
        select(Relationship)
        .where(Relationship.analysis_id == analysis_id)
        .order_by(Relationship.created_at, Relationship.id)
    )
    return list((await session.scalars(statement)).all())


@safe
async def set_relationship_verification(
    session: AsyncSession,
    relationship_id: str,
    *,
    verification_status: str,
    summary: str | None = None,
    notes: Sequence[str] | None = None,
) -> Relationship:
    """Record the outcome of evidence evaluation (C4).

    The status must be one of the contract's ``VERIFICATION_STATUSES`` —
    a repository-level vocabulary check, so an evaluation can never
    persist a status the API contract does not define. ``None`` leaves
    ``summary``/``notes`` untouched.
    """
    if verification_status not in VERIFICATION_STATUSES:
        raise ConflictError(
            f"Unknown verification status {verification_status!r}; "
            "allowed: " + ", ".join(VERIFICATION_STATUSES)
        )
    relationship = await session.get(Relationship, relationship_id)
    if relationship is None:
        raise NotFoundError(f"Relationship {relationship_id!r} does not exist.")

    relationship.verification_status = verification_status
    if summary is not None:
        relationship.summary = summary
    if notes is not None:
        relationship.notes = list(notes)
    await session.flush()
    return relationship


@safe
async def save_relationships(
    session: AsyncSession,
    analysis_id: str,
    items: Sequence[RelationshipInput | Mapping],
) -> list[Relationship]:
    """Persist relationship candidates for one analysis.

    Existing pairs are updated in place (last write wins); new pairs are
    inserted. Input order is preserved in the return value. Flushes at
    the end — commit/rollback belongs to the caller's ``session_scope``.
    """
    inputs = [coerce_relationship_input(item) for item in items]
    if not inputs:
        return []

    await _require_analysis(session, analysis_id)
    await _require_entities(
        session,
        {
            entity_id
            for item in inputs
            for entity_id in (item.source_entity_id, item.target_entity_id)
        },
    )

    existing = {
        (row.source_entity_id, row.target_entity_id): row
        for row in (
            await session.scalars(
                select(Relationship).where(Relationship.analysis_id == analysis_id)
            )
        ).all()
    }

    saved: list[Relationship] = []
    for item in inputs:
        key = (item.source_entity_id, item.target_entity_id)
        row = existing.get(key)
        if row is None:
            row = Relationship(
                id=item.relationship_id or None,
                analysis_id=analysis_id,
                source_entity_id=item.source_entity_id,
                target_entity_id=item.target_entity_id,
                label=item.label,
                verification_status=item.verification_status,
                summary=item.summary,
                notes=list(item.notes) if item.notes is not None else None,
            )
            session.add(row)
            existing[key] = row
        else:
            row.label = item.label
            row.verification_status = item.verification_status
            row.summary = item.summary
            row.notes = list(item.notes) if item.notes is not None else None
        saved.append(row)

    await session.flush()
    return saved
