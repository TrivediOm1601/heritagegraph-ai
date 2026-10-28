"""C2 — evidence repository (supporting evidence for relationships).

Signature note (pending agreement with Members 2 and 4):
``save_evidence`` **replaces** a relationship's evidence set (the
verification result is regenerated as a whole), then flushes; commit
stays with the caller's ``session_scope``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Evidence, Relationship
from ..db.session import ConflictError, NotFoundError, safe

__all__ = ["EvidenceInput", "save_evidence", "list_evidence"]


@dataclass(frozen=True)
class EvidenceInput:
    """One evidence item to persist (contract: ``EvidenceItem``)."""

    source: str
    excerpt: str | None = None
    url: str | None = None
    evidence_id: str | None = None


def coerce_evidence_input(item: EvidenceInput | Mapping) -> EvidenceInput:
    """Accept :class:`EvidenceInput`, plain dicts, or contract keys."""
    if isinstance(item, EvidenceInput):
        result = item
    elif isinstance(item, Mapping):
        def pick(*keys: str, default=None):
            for key in keys:
                if key in item:
                    return item[key]
            return default

        result = EvidenceInput(
            source=str(pick("source") or ""),
            excerpt=pick("excerpt"),
            url=pick("url"),
            evidence_id=pick("evidence_id", "id"),
        )
    else:
        raise ConflictError("Unsupported evidence payload.")

    if not result.source or not result.source.strip():
        raise ConflictError("Every evidence item needs a source.")
    return result


@safe
async def save_evidence(
    session: AsyncSession,
    relationship_id: str,
    items: Sequence[EvidenceInput | Mapping],
    *,
    replace: bool = True,
) -> list[Evidence]:
    """Persist the evidence set for one relationship.

    With ``replace=True`` (default) the previous items of this
    relationship are deleted first — a re-verification never leaves stale
    evidence behind. Raises :class:`NotFoundError` when the relationship
    does not exist (an evidence set is meaningless without its edge).
    """
    relationship = await session.get(Relationship, relationship_id)
    if relationship is None:
        raise NotFoundError(f"Relationship {relationship_id!r} does not exist.")

    prepared = [coerce_evidence_input(item) for item in items]

    if replace:
        previous = (
            await session.scalars(
                select(Evidence).where(Evidence.relationship_id == relationship_id)
            )
        ).all()
        for row in previous:
            await session.delete(row)

    rows = [
        Evidence(
            id=item.evidence_id or None,
            relationship_id=relationship_id,
            source=item.source.strip(),
            excerpt=item.excerpt,
            url=item.url,
        )
        for item in prepared
    ]
    session.add_all(rows)
    await session.flush()
    return rows


@safe
async def list_evidence(
    session: AsyncSession,
    relationship_id: str,
) -> list[Evidence]:
    """Read side for the evidence endpoint: ordered, stable output."""
    statement = (
        select(Evidence)
        .where(Evidence.relationship_id == relationship_id)
        .order_by(Evidence.created_at, Evidence.id)
    )
    return list((await session.scalars(statement)).all())
