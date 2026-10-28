"""D3 — candidate entities: type canonicalisation, conservative dedupe, ids.

Turns the vision model's raw entities into candidates that are safe to
persist and honest to display:

* **canonical types** — Module C's :data:`~app.db.models.ENTITY_TYPES`
  (``person, place, event, date, other``) is the vocabulary enforced by
  the database's CHECK constraint and by ``upsert_entities``; anything
  the model invents (``object``, ``organisation``, ...) is recorded as
  ``original_type`` and mapped to ``other`` *with a warning* rather than
  allowed to crash persistence or silently disappear;
* **conservative dedupe** — two candidates merge only when their names
  are **exact matches under the shared normalisation rule**
  (whitespace + casefold, Module C's ``normalize_name``) *and* their
  canonical types are equal. Different spellings are never merged;
  the same name under two different types is *ambiguous* — both entries
  are kept and a warning says so;
* **provenance** — the original extracted spelling, the original type,
  and the sources that produced the candidate (``vision``, plus ``ocr``
  when the name also occurs in the OCR text) are all preserved;
* **ids** — stable ``ent-1..ent-N`` ids in first-appearance order, which
  Module B's contract requires to be unique within one analysis (and
  which Module D's graph nodes later reference).

Nothing here ever assigns a verification status: a candidate is a
*proposal* until Module C4 evaluates its evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from ..db.models import ENTITY_TYPES
from ..schemas.entity import AnalysisEntity
from ..utils.text_normalization import name_in_text, normalize_name

__all__ = [
    "SOURCE_OCR",
    "SOURCE_VISION",
    "ENTITY_TYPE_SYNONYMS",
    "EntityCandidate",
    "canonicalize_entity_type",
    "build_candidates",
    "deduplicate",
    "to_analysis_entities",
]

#: Provenance labels (kept stable — they may surface in logs/tests).
SOURCE_VISION = "vision"
SOURCE_OCR = "ocr"

#: Fallback category when the model reports a type outside the vocabulary.
CANONICAL_FALLBACK = "other"

#: Documented heritage vocabulary that maps onto the canonical contract
#: types (Repair R5). The engineering plan names monument, dynasty,
#: document, inscription and organization; the frozen contract (and the
#: database CHECK) only accept ``person, place, event, date, other`` — so
#: these well-understood synonyms canonicalise *without* the
#: "unrecognised type" warning, while the reported label is still kept on
#: the candidate (``original_type``). Anything else keeps the warning.
ENTITY_TYPE_SYNONYMS: dict[str, str] = {
    # heritage sites and structures are places
    "monument": "place",
    "monuments": "place",
    "heritage site": "place",
    "site": "place",
    "sites": "place",
    "building": "place",
    "buildings": "place",
    "structure": "place",
    "structures": "place",
    "location": "place",
    # plan categories without a dedicated contract type → 'other'
    "dynasty": "other",
    "dynasties": "other",
    "document": "other",
    "documents": "other",
    "inscription": "other",
    "inscriptions": "other",
    "organization": "other",
    "organizations": "other",
    "organisation": "other",
    "organisations": "other",
}


@dataclass(frozen=True)
class EntityCandidate:
    """One extracted entity with its provenance, before id assignment."""

    #: Display name: the extracted text, edge-trimmed only (internal
    #: spelling and punctuation preserved exactly as extracted).
    name: str
    #: Canonical type from :data:`ENTITY_TYPES` (safe for the DB).
    entity_type: str
    #: The type the model actually reported, when it differed.
    original_type: str | None
    #: 0..1 confidence as reported (``None`` = not reported — never invented).
    confidence: float | None
    #: Where this value came from: "vision", optionally + "ocr".
    sources: tuple[str, ...]
    #: Shared normalisation key (Module C's rule) — the dedupe identity.
    normalized_name: str

    @classmethod
    def create(
        cls,
        *,
        name: str,
        entity_type: str,
        original_type: str | None,
        confidence: float | None,
        sources: Sequence[str],
    ) -> "EntityCandidate":
        trimmed = name.strip()
        return cls(
            name=trimmed,
            entity_type=entity_type,
            original_type=original_type,
            confidence=confidence,
            sources=tuple(dict.fromkeys(sources)),
            normalized_name=normalize_name(trimmed),
        )


def canonicalize_entity_type(raw: str) -> tuple[str, str | None]:
    """Map a reported type onto the canonical vocabulary.

    Returns ``(canonical_type, original_if_remapped)`` — case/whitespace
    variants normalise silently (pure normalisation); a documented
    heritage synonym (``monument`` → ``place``, ``inscription`` →
    ``other``, …) maps without a warning; any other unknown type becomes
    ``other`` and is returned as the second element so the caller can
    raise one aggregated warning.
    """
    cleaned = (raw or "").strip()
    lowered = cleaned.casefold()
    if lowered in ENTITY_TYPES:
        return lowered, None
    synonym = ENTITY_TYPE_SYNONYMS.get(lowered)
    if synonym is not None:
        return synonym, cleaned
    return CANONICAL_FALLBACK, cleaned


def build_candidates(
    vision_entities: Sequence,
    *,
    ocr_text: str | None = None,
) -> tuple[list[EntityCandidate], list[str]]:
    """Convert validated vision entities into candidates (+ warnings).

    ``vision_entities`` are :class:`~app.integrations.vision_model.VisionEntity`
    objects (already pydantic-validated by D2). ``ocr_text``, when
    present, corroborates each name that occurs in it — the candidate
    then carries both sources. Entities whose name normalises to nothing
    are discarded with a warning (Module C's repository rejects blank
    names outright).
    """
    candidates: list[EntityCandidate] = []
    warnings: list[str] = []
    unknown_types: set[str] = set()  # as-reported forms, for one warning

    for entity in vision_entities:
        display_name = (entity.name or "").strip()
        if not normalize_name(display_name):
            warnings.append("An extracted entity with an empty name was discarded.")
            continue

        canonical, remapped = canonicalize_entity_type(entity.type)
        if remapped is not None and remapped.casefold() not in ENTITY_TYPE_SYNONYMS:
            # One aggregated warning instead of N identical ones.
            unknown_types.add((entity.type or "").strip())

        sources = [SOURCE_VISION]
        if ocr_text and name_in_text(display_name, ocr_text):
            sources.append(SOURCE_OCR)

        candidates.append(
            EntityCandidate.create(
                name=display_name,
                entity_type=canonical,
                original_type=remapped,
                confidence=entity.confidence,
                sources=sources,
            )
        )

    if unknown_types:
        reported = ", ".join(f'"{value}"' for value in sorted(unknown_types))
        warnings.append(
            f"Unrecognised entity types {reported} were recorded as "
            f'"{CANONICAL_FALLBACK}" (allowed: {", ".join(ENTITY_TYPES)}).'
        )
    return candidates, warnings


def _merge_confidence(a: float | None, b: float | None) -> float | None:
    """Highest reported confidence; ``None`` means unreported, not zero."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def deduplicate(
    candidates: Sequence[EntityCandidate],
) -> tuple[list[EntityCandidate], list[str]]:
    """Merge exact matches; keep ambiguous ones separate (+ warnings).

    Exact = same normalised name **and** same canonical type. Same name
    under different types is ambiguous: every entry survives, with a
    warning naming the types so a reviewer can adjudicate.
    """
    merged: list[EntityCandidate] = []
    index: dict[tuple[str, str], int] = {}
    # normalised name -> (first display name, types seen)
    display: dict[str, str] = {}
    types_by_name: dict[str, set[str]] = {}

    for candidate in candidates:
        key = (candidate.normalized_name, candidate.entity_type)
        existing = index.get(key)
        if existing is None:
            index[key] = len(merged)
            merged.append(candidate)
        else:
            current = merged[existing]
            merged[existing] = replace(
                current,
                confidence=_merge_confidence(current.confidence, candidate.confidence),
                sources=tuple(dict.fromkeys(current.sources + candidate.sources)),
            )
        display.setdefault(candidate.normalized_name, candidate.name)
        types_by_name.setdefault(candidate.normalized_name, set()).add(candidate.entity_type)

    warnings = [
        f'Ambiguous name "{display[key]}" was extracted with multiple types '
        f'({", ".join(sorted(types))}); entries kept separate.'
        for key, types in sorted(types_by_name.items(), key=lambda item: display[item[0]])
        if len(types) > 1
    ]
    return merged, warnings


def to_analysis_entities(candidates: Sequence[EntityCandidate]) -> list[AnalysisEntity]:
    """Assign stable per-analysis ids and emit the B2 schema entities."""
    return [
        AnalysisEntity(
            id=f"ent-{position}",
            name=candidate.name,
            type=candidate.entity_type,
            confidence=candidate.confidence,
        )
        for position, candidate in enumerate(candidates, start=1)
    ]
