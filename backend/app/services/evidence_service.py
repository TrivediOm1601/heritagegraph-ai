"""C4 — evidence association and verification persistence (Module C).

Accepts persisted relationship *candidates* (Module D's proposals, stored
by :func:`app.repositories.relationship_repository.save_relationships`),
retrieves the source records that mention their entities, evaluates
whether that material actually supports the claim, and persists both the
evidence associations and the resulting verification status.

**Acceptance rule:** a relationship is never marked evidence-supported
merely because a model proposed it. The candidate's incoming status is
always *overwritten* by the evaluation, and ``verified`` is structurally
unreachable unless at least one retrieved source record documents **both**
entities within :data:`CO_OCCURRENCE_WINDOW` characters of each other —
with no contradiction cue in that span.

Verdicts (all contract ``VERIFICATION_STATUSES``):

======================  ====================================================
``verified``            a supporting co-mention record exists.
``disputed``            a co-mention carries a contradiction cue — this
                        beats support, and the contradicting excerpt is
                        preserved as evidence.
``unsupported``         source records mention the entities individually
                        but none documents the relationship (the
                        considered excerpts are preserved as evidence).
``unverified``          either *no* source material at all (missing —
                        evidence list empty, notes say so explicitly), or
                        *ambiguous* material that mentions both entities
                        but never in a connected context.
======================  ====================================================

Evaluation is a **deterministic lexical heuristic** (no model calls —
those belong to Module D): co-occurrence within a window plus a small,
documented contradiction-cue list. It is deliberately conservative:
absence of evidence never becomes ``verified``.

Signature note (pending agreement with Members 2 and 4): ``session`` is
the first argument; the service flushes but never commits — commit stays
with the caller's ``session_scope`` (B3).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import VERIFICATION_STATUSES, Entity, HistoricalRecord, Relationship
from ..db.session import NotFoundError
from ..repositories.evidence_repository import save_evidence
from ..repositories.relationship_repository import (
    get_relationship,
    list_relationships,
    set_relationship_verification,
)
from .retrieval_service import search_historical_records

__all__ = [
    "CO_OCCURRENCE_WINDOW",
    "MAX_EVIDENCE_ITEMS",
    "MAX_PROBE_RECORDS",
    "CONTRADICTION_CUES",
    "EvidenceLink",
    "EvidenceEvaluation",
    "evaluate_relationship_evidence",
    "evaluate_analysis_evidence",
]

#: Two entity names count as connected when their combined span (the text
#: from the first name to the end of the second) is at most this many
#: characters — roughly a sentence or two.
CO_OCCURRENCE_WINDOW = 240

#: Evidence rows persisted per relationship (first, in classification
#: order) — the evidence panel shows a readable set, not everything.
MAX_EVIDENCE_ITEMS = 5

#: How many records each entity-name probe may contribute.
MAX_PROBE_RECORDS = 10

#: Conservative lexical cues checked ONLY inside a co-mention span.
#: Chosen to be rare in neutral prose; a hit means the source material
#: actively contradicts the proposed relationship.
CONTRADICTION_CUES: tuple[str, ...] = (
    "disputed",
    "disputes",
    "debunked",
    "debunks",
    "myth",
    "false claim",
    "fabricated",
    "misattributed",
    "wrongly attributed",
    "attributed in error",
    "no evidence",
    "never met",
    "did not",
    "no connection",
    "unrelated to",
    "contradict",
    "denied by",
    "rejected by",
)


@dataclass(frozen=True)
class EvidenceLink:
    """One source record tied to the relationship, with its excerpt."""

    record_id: str
    title: str
    #: Publisher of the record — the traceable source (contract: ``EvidenceItem.source``).
    source: str
    excerpt: str | None
    #: Canonical link to the cited material (contract: ``EvidenceItem.url``);
    #: ``None`` when the record has no URL — never invented.
    url: str | None = None


@dataclass(frozen=True)
class EvidenceEvaluation:
    """Outcome of evaluating one relationship against the record store."""

    relationship_id: str
    status: str
    summary: str
    notes: list[str]
    evidence: list[EvidenceLink]
    records_considered: int
    supporting: int
    inconclusive: int
    contradicting: int


def _normalize(name: str) -> str:
    return " ".join(name.split()).casefold()


def _haystack(record: HistoricalRecord) -> str:
    return f"{record.title}\n{record.content or ''}"


def _find_all(text: str, term: str) -> list[int]:
    positions: list[int] = []
    start = text.find(term)
    while start != -1:
        positions.append(start)
        start = text.find(term, start + 1)
    return positions


def _nearest_pair(haystack_cf: str, source_cf: str, target_cf: str) -> tuple[int, int] | None:
    """(start, end) of the closest source/target name span, or None."""
    source_positions = _find_all(haystack_cf, source_cf)
    if not source_positions:
        return None
    target_positions = _find_all(haystack_cf, target_cf)
    if not target_positions:
        return None

    best: tuple[int, int, int] | None = None  # (span, start, end)
    for source_at in source_positions:
        for target_at in target_positions:
            start = min(source_at, target_at)
            end = max(source_at + len(source_cf), target_at + len(target_cf))
            span = end - start
            if best is None or span < best[0]:
                best = (span, start, end)
    assert best is not None
    return best[1], best[2]


def _window(record: HistoricalRecord, start: int, end: int, *, lead: int = 60, tail: int = 90) -> str:
    """Original-case excerpt around [start, end) with context and ellipses."""
    text = _haystack(record)
    from_start = max(0, start - lead)
    to_end = min(len(text), end + tail)
    prefix = "…" if from_start > 0 else ""
    suffix = "…" if to_end < len(text) else ""
    return f"{prefix}{text[from_start:to_end].strip()}{suffix}"


def _single_term_window(record: HistoricalRecord, term: str) -> str | None:
    """Excerpt around the first occurrence of one entity name."""
    haystack = _haystack(record)
    at = haystack.casefold().find(term)
    if at == -1:
        return None
    return _window(record, at, at + len(term))


def _link(record: HistoricalRecord, excerpt: str | None) -> EvidenceLink:
    return EvidenceLink(
        record_id=record.id,
        title=record.title,
        source=record.source,
        excerpt=excerpt,
        url=record.url,
    )


async def evaluate_relationship_evidence(
    session: AsyncSession,
    relationship_id: str,
) -> EvidenceEvaluation:
    """Evaluate one relationship and persist evidence + verification.

    Replaces the relationship's previous evidence set and overwrites its
    verification status, summary and notes with this evaluation's
    outcome. Flushes only; the caller commits (``session_scope``).
    """
    relationship = await get_relationship(session, relationship_id)
    if relationship is None:
        raise NotFoundError(f"Relationship {relationship_id!r} does not exist.")

    source_entity = await session.get(Entity, relationship.source_entity_id)
    target_entity = await session.get(Entity, relationship.target_entity_id)
    if source_entity is None or target_entity is None:
        # Unreachable through the schema's foreign keys; guarded so a
        # broken relationship degrades to an explicit error, never a
        # fabricated verdict.
        raise NotFoundError(
            f"Relationship {relationship_id!r} references a missing entity."
        )

    source_name = _normalize(source_entity.name)
    target_name = _normalize(target_entity.name)

    # Retrieve associated source records through the C3 retrieval
    # service (normalisation, spelling variants, ranking included).
    records: dict[str, HistoricalRecord] = {}
    for name in (source_entity.name, target_entity.name):
        for hit in await search_historical_records(
            session, name, limit=MAX_PROBE_RECORDS
        ):
            records.setdefault(hit.record.id, hit.record)

    supporting: list[HistoricalRecord] = []
    inconclusive: list[HistoricalRecord] = []
    contradicting: list[HistoricalRecord] = []
    considered: list[HistoricalRecord] = []  # records mentioning neither name together

    same_name = source_name == target_name
    for record in records.values():
        haystack_cf = _haystack(record).casefold()
        if same_name:
            # Two distinct entities sharing one name can never be
            # disambiguated lexically — never a verified verdict.
            (inconclusive if source_name in haystack_cf else considered).append(record)
            continue

        pair = _nearest_pair(haystack_cf, source_name, target_name)
        if pair is None:
            considered.append(record)
            continue
        start, end = pair
        # Cues are checked in the co-mention span plus a short margin:
        # contradictions usually follow the claim ("… Analytical Engine is
        # disputed by …") or preface it ("Disputed by …, Ada Lovelace …").
        cue_region = haystack_cf[
            max(0, start - 40) : min(len(haystack_cf), end + 120)
        ]
        if any(cue in cue_region for cue in CONTRADICTION_CUES):
            contradicting.append(record)
        elif (end - start) <= CO_OCCURRENCE_WINDOW:
            supporting.append(record)
        else:
            inconclusive.append(record)

    # --- verdict (contradiction beats support beats absence) --------------
    if contradicting:
        status = "disputed"
        summary = (
            f"Contradicted by {len(contradicting)} source record(s); "
            "the contradictory excerpt is preserved as evidence."
        )
        links = [
            _link(record, _window_for_pair(record, source_name, target_name))
            for record in contradicting[:MAX_EVIDENCE_ITEMS]
        ]
    elif supporting:
        status = "verified"
        summary = (
            f"Supported by {len(supporting)} source record(s) that document "
            "both entities together."
        )
        links = [
            _link(record, _window_for_pair(record, source_name, target_name))
            for record in supporting[:MAX_EVIDENCE_ITEMS]
        ]
    elif inconclusive:
        status = "unverified"
        summary = (
            "Inconclusive: source material mentions both entities but never "
            "in a connected context."
        )
        links = [
            _link(record, _single_term_window(record, source_name))
            for record in inconclusive[:MAX_EVIDENCE_ITEMS]
        ]
    elif records:
        status = "unsupported"
        summary = (
            f"{len(records)} source record(s) mention the entities, but none "
            "documents this relationship."
        )
        links = []
        for record in considered[:MAX_EVIDENCE_ITEMS]:
            excerpt = _single_term_window(record, source_name) or _single_term_window(
                record, target_name
            )
            links.append(_link(record, excerpt))
    else:
        status = "unverified"
        summary = "No source material found; verification was impossible."
        links = []

    notes = [
        f"Evaluated {len(records)} source record(s) from the historical record store.",
        (
            f"Supporting: {len(supporting)}; inconclusive: {len(inconclusive)}; "
            f"contradicting: {len(contradicting)}."
        ),
    ]
    if not records:
        notes.append(
            f"No records matched '{source_entity.name}' or '{target_entity.name}'."
        )
    if same_name and records:
        notes.append(
            "Both entities share the same name; automatic verification was skipped."
        )
    if contradicting and supporting:
        notes.append(
            f"{len(supporting)} supporting record(s) also found; "
            "contradiction takes precedence."
        )

    # Structural acceptance guard: verified is unreachable without proof.
    assert status in VERIFICATION_STATUSES
    if status == "verified":
        assert supporting, "verified requires at least one supporting record"

    # --- persist ----------------------------------------------------------
    await save_evidence(
        session,
        relationship_id,
        [
            {"source": link.source, "excerpt": link.excerpt, "url": link.url}
            for link in links
        ],
    )
    await set_relationship_verification(
        session,
        relationship_id,
        verification_status=status,
        summary=summary,
        notes=notes,
    )

    return EvidenceEvaluation(
        relationship_id=relationship_id,
        status=status,
        summary=summary,
        notes=notes,
        evidence=links,
        records_considered=len(records),
        supporting=len(supporting),
        inconclusive=len(inconclusive),
        contradicting=len(contradicting),
    )


async def evaluate_analysis_evidence(
    session: AsyncSession,
    analysis_id: str,
) -> list[EvidenceEvaluation]:
    """Evaluate every relationship of one analysis (B3 convenience)."""
    relationships = await list_relationships(session, analysis_id)
    return [
        await evaluate_relationship_evidence(session, relationship.id)
        for relationship in relationships
    ]


def _window_for_pair(record: HistoricalRecord, source_name: str, target_name: str) -> str:
    """Excerpt around the nearest source/target span (recomputed to slice)."""
    haystack_cf = _haystack(record).casefold()
    pair = _nearest_pair(haystack_cf, source_name, target_name)
    assert pair is not None  # classification already found this pair
    return _window(record, pair[0], pair[1])