"""C2 — analysis-run repository (analysis metadata + entity links).

Signature note (pending agreement with Members 2 and 4):

* ``create_analysis_run`` accepts an explicit id (the API's
  ``analysisId``) or generates a UUID,
* ``update_analysis_run`` uses an ``UNSET`` sentinel, so ``None`` can
  *clear* ``error``/``progress``/… while an omitted kwarg is left alone,
* ``attach_entities`` links canonical entities to a run with per-run
  confidence (upsert on re-link).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    ANALYSIS_STATES,
    AnalysisEntity,
    AnalysisRun,
    Entity,
)
from ..db.session import ConflictError, NotFoundError, safe

__all__ = [
    "UNSET",
    "EntityLink",
    "create_analysis_run",
    "get_analysis_run",
    "update_analysis_run",
    "attach_entities",
    "list_analysis_entities",
]


class _Unset:
    """Sentinel: kwarg not provided (distinct from an explicit ``None``)."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


@dataclass(frozen=True)
class EntityLink:
    """One entity attached to an analysis run."""

    entity_id: str
    confidence: float | None = None


def _coerce_link(item: EntityLink | Mapping) -> EntityLink:
    if isinstance(item, EntityLink):
        return item
    if isinstance(item, Mapping):
        entity_id = item.get("entity_id") or item.get("id")
        if not entity_id:
            raise ConflictError("An entity link needs an entity id.")
        confidence = item.get("confidence")
        return EntityLink(entity_id=str(entity_id), confidence=confidence)
    raise ConflictError("Unsupported entity link payload.")


@safe
async def create_analysis_run(
    session: AsyncSession,
    *,
    analysis_id: str | None = None,
    original_filename: str | None = None,
    content_type: str | None = None,
    status: str = "queued",
    user_id: str | None = None,
) -> AnalysisRun:
    """Create one analysis run (contract states only).

    ``user_id`` is the account that owns the run. It is always taken from
    the validated session by the route — never from a client-supplied
    field — and is what every read/export path filters on.
    """
    if status not in ANALYSIS_STATES:
        raise ConflictError(
            f"Unknown analysis status {status!r}; allowed: "
            + ", ".join(ANALYSIS_STATES)
        )
    run = AnalysisRun(
        id=analysis_id or str(uuid.uuid4()),
        status=status,
        original_filename=original_filename,
        content_type=content_type,
        user_id=user_id,
    )
    session.add(run)
    await session.flush()
    return run


@safe
async def get_analysis_run(
    session: AsyncSession,
    analysis_id: str,
) -> AnalysisRun | None:
    """Fetch one run by id (``None`` when it does not exist)."""
    return await session.get(AnalysisRun, analysis_id)


@safe
async def update_analysis_run(
    session: AsyncSession,
    analysis_id: str,
    *,
    status: str | None | _Unset = UNSET,
    progress: float | None | _Unset = UNSET,
    extracted_text: str | None | _Unset = UNSET,
    interpretation: str | None | _Unset = UNSET,
    briefing: dict | None | _Unset = UNSET,
    warnings: list[str] | None | _Unset = UNSET,
    error: str | None | _Unset = UNSET,
) -> AnalysisRun:
    """Partial update; omitted kwargs stay untouched, ``None`` clears."""
    run = await session.get(AnalysisRun, analysis_id)
    if run is None:
        raise NotFoundError(f"Analysis {analysis_id!r} does not exist.")

    if not isinstance(status, _Unset):
        if status is None:
            raise ConflictError("Analysis status cannot be empty.")
        if status not in ANALYSIS_STATES:
            raise ConflictError(
                f"Unknown analysis status {status!r}; allowed: "
                + ", ".join(ANALYSIS_STATES)
            )
        run.status = status
    if not isinstance(progress, _Unset):
        if progress is not None and not 0 <= progress <= 1:
            raise ConflictError("Progress must be between 0 and 1.")
        run.progress = progress
    if not isinstance(extracted_text, _Unset):
        run.extracted_text = extracted_text
    if not isinstance(interpretation, _Unset):
        run.interpretation = interpretation
    if not isinstance(briefing, _Unset):
        run.briefing = dict(briefing) if briefing is not None else None
    if not isinstance(warnings, _Unset):
        run.warnings = list(warnings) if warnings is not None else None
    if not isinstance(error, _Unset):
        run.error = error

    await session.flush()
    return run


@safe
async def attach_entities(
    session: AsyncSession,
    analysis_id: str,
    links: Sequence[EntityLink | Mapping],
) -> list[AnalysisEntity]:
    """Link canonical entities to a run with per-run confidence.

    Re-linking an entity updates its confidence (upsert via C1's
    composite primary key).
    """
    prepared = [_coerce_link(item) for item in links]
    if not prepared:
        return []

    if await session.get(AnalysisRun, analysis_id) is None:
        raise NotFoundError(f"Analysis {analysis_id!r} does not exist.")

    wanted = {link.entity_id for link in prepared}
    found = set(
        (await session.scalars(select(Entity.id).where(Entity.id.in_(wanted)))).all()
    )
    missing = sorted(wanted - found)
    if missing:
        shown = ", ".join(missing[:10])
        suffix = " …" if len(missing) > 10 else ""
        raise NotFoundError(f"Unknown entity id(s): {shown}{suffix}")

    existing = {
        row.entity_id: row
        for row in (
            await session.scalars(
                select(AnalysisEntity).where(AnalysisEntity.analysis_id == analysis_id)
            )
        ).all()
    }

    rows: list[AnalysisEntity] = []
    for link in prepared:
        row = existing.get(link.entity_id)
        if row is None:
            row = AnalysisEntity(
                analysis_id=analysis_id,
                entity_id=link.entity_id,
                confidence=link.confidence,
            )
            session.add(row)
            existing[link.entity_id] = row
        elif link.confidence is not None:
            row.confidence = link.confidence
        rows.append(row)

    await session.flush()
    return rows


@safe
async def list_analysis_entities(
    session: AsyncSession,
    analysis_id: str,
) -> list[tuple[Entity, AnalysisEntity]]:
    """(entity, link) for every extracted entity of one analysis, ordered
    by name so the graph's node list is deterministic.

    Read side for the graph endpoint (B4): an analysis with no entities
    returns ``[]`` — an empty graph is not an error.
    """
    statement = (
        select(Entity, AnalysisEntity)
        .join(AnalysisEntity, AnalysisEntity.entity_id == Entity.id)
        .where(AnalysisEntity.analysis_id == analysis_id)
        .order_by(Entity.name, Entity.entity_type)
    )
    return list((await session.execute(statement)).all())
