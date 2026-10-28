"""D4 — candidate relationships from extraction proposals.

Turns the vision model's proposed edges (``VisionExtraction.relationships``
from D2, carried through D3's ``ExtractionResult``) into candidates that
are safe to persist and honest to display.

**What creates an edge:** only an explicit proposal from the vision
model. Retrieved historical records may *corroborate* a proposal (both
endpoint names occur in one record — recorded as provenance), but they
never create an edge on their own: co-occurrence does not state a
relationship, and inventing edges from it would fabricate historical
claims (the D4 acceptance criterion).

**Validation (every failure is an exclusion + warning, never a silent
drop and never a fabricated edge):**

* endpoints must resolve to exactly one extracted entity under Module
  C's shared normalisation rule — if the exact key misses, a *conservative*
  approximate step (alphanumeric-only comparison) may still bind it when
  exactly one extracted entity matches (Repair R5: models routinely emit
  “Rani ki Vav” vs “Rani-ki-Vav”); every such resolution is reported in
  the warnings, and a name matching several entities is excluded as
  ambiguous rather than arbitrarily bound;
* self-loops are excluded (the schema's ``ck_relationships_no_self_loop``
  forbids them);
* the label — the relationship's *type* in the frozen contract, since
  neither ``GraphEdge`` nor ``relationships`` has a separate type field
  — is cleaned (control characters removed), blank labels become
  ``None``, and over-long labels are truncated to the schema/DB limit of
  512 characters with a warning;
* duplicate ordered pairs collapse onto one candidate (mirroring
  ``uq_relationships_pair``), merging confidence, sources and record
  provenance.

**Verification status:** every candidate starts at ``unverified`` and
this module never assigns ``verified``/``disputed``/``unsupported`` —
those verdicts belong exclusively to Module C4's evidence evaluation,
which B3 applies afterwards (``graph_service.build_graph(statuses=...)``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..db.models import HistoricalRecord
from ..integrations.vision_model import VisionRelationship
from ..schemas.entity import AnalysisEntity
from ..utils.text_normalization import name_in_text, normalize_name

__all__ = [
    "MAX_LABEL_LENGTH",
    "RelationshipCandidate",
    "clean_label",
    "generate_relationship_candidates",
]

#: Longest label kept — ``GraphEdge.label`` max_length and the DB's
#: ``_NAME`` column are both 512.
MAX_LABEL_LENGTH = 512

#: Provenance channels (stable strings; may surface in persisted notes).
SOURCE_VISION = "vision"
SOURCE_RECORD = "record"


@dataclass(frozen=True)
class RelationshipCandidate:
    """One validated, not-yet-evaluated edge proposal."""

    #: Stable, deterministic graph id (``rel-1..rel-N``) — also the id
    #: Module B's evidence endpoint will address (``GraphEdge.id``).
    id: str
    #: Endpoint **entity ids** (contract: ``GraphEdge.source/target``).
    source: str
    target: str
    #: Display names as extracted (for logs/notes; provenance of the value).
    source_name: str
    target_name: str
    #: Predicate shown on the edge; ``None`` when the model gave none/blank.
    label: str | None
    #: Model-reported confidence (``None`` = unreported, never invented).
    confidence: float | None
    #: Always "unverified" here — evaluation overwrites it later.
    verification_status: str
    #: Provenance channels: starts with "vision", gains "record" when at
    #: least one retrieved record documents both endpoints.
    sources: tuple[str, ...]
    #: Ids of the corroborating records (both names occur in the same record).
    record_ids: tuple[str, ...]


def clean_label(raw: str | None) -> tuple[str | None, bool]:
    """Validate one label: single-line, printable, within the schema limit.

    Returns ``(label, truncated)`` — ``None``/``False`` for an absent or
    blank label (the contract allows a label-less edge).
    """
    if raw is None:
        return None, False
    # Control characters become spaces (never glued-together words),
    # then whitespace collapses to single spaces.
    printable = "".join(ch if ch.isprintable() else " " for ch in raw)
    cleaned = " ".join(printable.split())
    if not cleaned:
        return None, False
    if len(cleaned) > MAX_LABEL_LENGTH:
        return cleaned[:MAX_LABEL_LENGTH], True
    return cleaned, False


def _record_haystack(record: HistoricalRecord) -> str:
    return f"{record.title}\n{record.content or ''}"


def _loose_key(name: str) -> str:
    """Alphanumeric-only comparison key ("Rani-ki-Vav" == "rani ki vav").

    Deliberately weaker than Module C's normalisation: used only as a
    second pass when the exact key missed, and only trusted when it
    matches exactly one extracted entity.
    """
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def generate_relationship_candidates(
    proposals: Sequence[VisionRelationship],
    entities: Sequence[AnalysisEntity],
    *,
    records: Sequence[HistoricalRecord] = (),
) -> tuple[list[RelationshipCandidate], list[str]]:
    """Validate proposals against extracted entities (+ record provenance).

    ``records`` are Module C's retrieval results (already fetched by B3)
    used **only** to corroborate proposals whose both endpoints occur in
    one record. Returns ``(candidates, warnings)`` in proposal order
    with stable ``rel-1..N`` ids; every discarded proposal is reported
    in ``warnings`` — nothing is dropped silently.
    """
    warnings: list[str] = []

    # Resolution index under Module C's shared normalisation rule.
    by_name: dict[str, list[AnalysisEntity]] = {}
    for entity in entities:
        by_name.setdefault(normalize_name(entity.name), []).append(entity)
    # Second-pass index for conservative approximate resolution (R5).
    by_loose: dict[str, list[AnalysisEntity]] = {}
    for entity in entities:
        key = _loose_key(entity.name)
        if key:
            by_loose.setdefault(key, []).append(entity)

    def _resolve(raw: str) -> tuple[list[AnalysisEntity], bool]:
        """(matches, approximated): exact key first, then unique loose hit."""
        exact = by_name.get(normalize_name(raw), [])
        if exact:
            return exact, False
        loose = by_loose.get(_loose_key(raw), [])
        if len(loose) == 1:
            return loose, True
        return loose, False

    missing: set[str] = set()
    ambiguous: set[str] = set()
    approximated: list[tuple[str, str]] = []  # (proposal spelling, entity name)
    self_loops = 0
    empty_endpoints = 0
    truncated_labels = 0

    resolved: list[tuple[AnalysisEntity, AnalysisEntity, VisionRelationship]] = []
    for proposal in proposals:
        source_raw = (proposal.source or "").strip()
        target_raw = (proposal.target or "").strip()
        if not source_raw or not target_raw:
            empty_endpoints += 1
            continue

        source_matches, source_loose = _resolve(source_raw)
        target_matches, target_loose = _resolve(target_raw)
        if not source_matches or not target_matches:
            missing.update(name for name, found in
                           ((source_raw, source_matches), (target_raw, target_matches))
                           if not found)
            continue
        if len(source_matches) > 1 or len(target_matches) > 1:
            ambiguous.update(
                name for name, found in
                ((source_raw, source_matches), (target_raw, target_matches))
                if len(found) > 1
            )
            continue

        source_entity, target_entity = source_matches[0], target_matches[0]
        if source_loose:
            approximated.append((source_raw, source_entity.name))
        if target_loose:
            approximated.append((target_raw, target_entity.name))
        if source_entity.id == target_entity.id:
            self_loops += 1
            continue
        resolved.append((source_entity, target_entity, proposal))

    # --- provenance: both endpoints co-mentioned in one record ------------
    def corroboration(source_entity: AnalysisEntity, target_entity: AnalysisEntity) -> tuple[str, ...]:
        return tuple(
            record.id
            for record in records
            if name_in_text(source_entity.name, _record_haystack(record))
            and name_in_text(target_entity.name, _record_haystack(record))
        )

    # --- dedupe on the DB's unique ordered pair ---------------------------
    merged: list[dict] = []
    index: dict[tuple[str, str], int] = {}
    for source_entity, target_entity, proposal in resolved:
        key = (source_entity.id, target_entity.id)
        record_ids = tuple(corroboration(source_entity, target_entity))
        sources = (SOURCE_VISION,) + ((SOURCE_RECORD,) if record_ids else ())
        label, truncated = clean_label(proposal.label)
        if truncated:
            truncated_labels += 1

        existing = index.get(key)
        if existing is None:
            index[key] = len(merged)
            merged.append(
                {
                    "source": source_entity,
                    "target": target_entity,
                    "label": label,
                    "confidence": proposal.confidence,
                    "sources": sources,
                    "record_ids": record_ids,
                }
            )
        else:
            current = merged[existing]
            if current["label"] is None and label is not None:
                current["label"] = label
            current["confidence"] = max(
                (c for c in (current["confidence"], proposal.confidence) if c is not None),
                default=None,
            )
            current["sources"] = tuple(dict.fromkeys(current["sources"] + sources))
            current["record_ids"] = tuple(
                dict.fromkeys(current["record_ids"] + record_ids)
            )

    candidates = [
        RelationshipCandidate(
            id=f"rel-{position}",
            source=item["source"].id,
            target=item["target"].id,
            source_name=item["source"].name,
            target_name=item["target"].name,
            label=item["label"],
            confidence=item["confidence"],
            verification_status="unverified",
            sources=item["sources"],
            record_ids=item["record_ids"],
        )
        for position, item in enumerate(merged, start=1)
    ]

    # --- warnings: every exclusion is reported, aggregated ----------------
    if empty_endpoints:
        warnings.append(
            "Relationship proposals with an empty endpoint were discarded "
            f"({empty_endpoints})."
        )
    if approximated:
        unique_pairs = sorted(set(approximated))
        warnings.append(
            "Relationship endpoints were matched to extracted entities after "
            "ignoring case and punctuation: "
            + ", ".join(f'"{raw}" → "{entity}"' for raw, entity in unique_pairs)
            + "."
        )
    if missing:
        warnings.append(
            "Relationship proposals referencing unknown entities were "
            "discarded: " + ", ".join(f'"{name}"' for name in sorted(missing)) + "."
        )
    if ambiguous:
        warnings.append(
            "Relationship proposals whose endpoint matches several entities "
            "were discarded: "
            + ", ".join(f'"{name}"' for name in sorted(ambiguous))
            + "."
        )
    if self_loops:
        warnings.append(
            f"Self-referencing relationship proposals were discarded ({self_loops})."
        )
    if truncated_labels:
        warnings.append(
            f"Relationship labels longer than {MAX_LABEL_LENGTH} characters "
            f"were shortened ({truncated_labels})."
        )
    return candidates, warnings