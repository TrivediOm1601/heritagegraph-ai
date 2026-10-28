"""C3 — historical retrieval service.

Sits above :func:`app.repositories.record_repository.search_records` and
adds the behaviour the product needs on top of raw SQL search:

* **query normalisation** — casefold + collapsed whitespace (the exact
  string the API echoes back as ``SearchResponse.query``),
* **alternate-spelling expansion** — a small, documented table of
  orthographic pairs (colour/color, programme/program, …) so either
  spelling finds the record,
* **multi-term matching** — individual terms are searched too, so
  \"babbage mathematician\" finds a record that mentions both words
  without them being adjacent,
* **deterministic relevance scoring** (0..1, contract-compatible) and
  **excerpts** — a short window of the record's own text around the
  matched term,
* **opt-in live sources** (Repair R2) — with ``HG_LIVE_SOURCES=1`` the
  query also runs against Wikipedia's public search API
  (:mod:`app.integrations.wikipedia`), and those citable hits are merged
  into the same ranking. Live records are transient (never persisted),
  carry their canonical URL, and degrade to “local only” on any
  outage — a source failure never fabricates results and never breaks
  local search,
* contract-shaped output via :meth:`RetrievedRecord.to_match` so Member
  2's ``/api/search`` handler can return ``HistoricalMatch`` objects
  without reshaping anything.

An empty query, or a query with no relevant record, returns ``[]`` — an
empty result set means *nothing matched*, never a failure (failures stay
repository/API errors).

Signature note (pending agreement with Members 2 and 4): ``session`` is
the first argument, mirroring the repositories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import HistoricalRecord
from ..integrations.wikipedia import live_sources_enabled, search_wikipedia
from ..repositories.record_repository import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    search_records,
)

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_VARIANTS",
    "RetrievedRecord",
    "normalize_query",
    "relevance_label",
    "spelling_variants",
    "search_historical_records",
]

#: Default page size for callers that do not choose one.
DEFAULT_LIMIT = DEFAULT_SEARCH_LIMIT

#: Safety cap on alternate-spelling query variants (term-substitution
#: products are capped so a pathological query cannot fan out).
MAX_VARIANTS = 8

#: How many individual terms of a multi-term query are probed on their
#: own (non-adjacent terms must still match, but a pathological query
#: must not fan out into hundreds of LIKE scans).
MAX_TERM_PROBES = 6

#: Individual terms shorter than this are NOT probed/scored on their own:
#: a 2-character token like "ki" matches inside countless unrelated words
#: ("machine"), which pulled irrelevant records into multi-term results
#: (found in the R2 end-to-end run — an unrelated record must not appear
#: just because the database is non-empty). A query made *only* of short
#: terms keeps its full text as the probe, so an explicit 2-character
#: search still works.
MIN_TERM_LENGTH = 3

#: Documented orthographic pairs (both directions). Deliberately small:
#: only spellings the curated dataset actually contains, so expansion can
#: never invent relevance.
_SPELLING_VARIANTS: dict[str, tuple[str, ...]] = {
    "colour": ("color",),
    "color": ("colour",),
    "programme": ("program",),
    "program": ("programme",),
    "analyse": ("analyze",),
    "analyze": ("analyse",),
    "centre": ("center",),
    "center": ("centre",),
    "organise": ("organize",),
    "organize": ("organise",),
    "defence": ("defense",),
    "defense": ("defence",),
    "travelling": ("traveling",),
    "traveling": ("travelling",),
    "modelling": ("modeling",),
    "modeling": ("modelling",),
    "licence": ("license",),
    "license": ("licence",),
}

#: Excerpt window: characters before/after the matched term.
_EXCERPT_LEAD = 60
_EXCERPT_TAIL = 90
_FALLBACK_EXCERPT = 140

#: How many live (Wikipedia) hits may join one query's candidate pool.
LIVE_SOURCE_LIMIT = 5
#: Source label for live hits (contract: HistoricalMatch.source).
LIVE_SOURCE_NAME = "Wikipedia"


@dataclass(frozen=True)
class RetrievedRecord:
    """One search hit: the stored record plus retrieval metadata."""

    record: HistoricalRecord
    excerpt: str | None
    score: float

    def to_match(self) -> dict[str, Any]:
        """Contract shape: ``HistoricalMatch`` exactly as the API returns it."""
        return {
            "id": self.record.id,
            "title": self.record.title,
            "year": self.record.year,
            "score": self.score,
            "source": self.record.source,
            "url": self.record.url,
            "excerpt": self.excerpt,
            "relevance": relevance_label(self.score),
        }


def normalize_query(query: str | None) -> str:
    """Casefold and collapse whitespace; ``None``/blank becomes ``\"\"``."""
    return " ".join((query or "").split()).casefold()


def spelling_variants(query: str) -> list[str]:
    """Query strings to try: the query itself plus spelling substitutions.

    Each term that has documented variants is substituted independently
    (cartesian product, capped at :data:`MAX_VARIANTS`), preserving term
    order — ``\"colour printing\"`` also yields ``\"color printing\"``.
    """
    terms = query.split()
    if not terms:
        return []
    products: list[tuple[str, ...]] = [tuple(terms)]
    for index, term in enumerate(terms):
        options = (term,) + _SPELLING_VARIANTS.get(term, ())
        if len(options) == 1:
            continue
        expanded = [
            product[:index] + (option,) + product[index + 1 :]
            for product in products
            for option in options
        ]
        # De-duplicate while preserving order, then cap the fan-out.
        unique: list[tuple[str, ...]] = []
        for candidate in expanded:
            if candidate not in unique:
                unique.append(candidate)
        products = unique[:MAX_VARIANTS]
    joined = [" ".join(product) for product in products]
    return list(dict.fromkeys(joined))  # order-preserving de-dup


#: Scoring weights (see :func:`_score`). They sum to 1.0 so a fully
#: matching record from the highest-quality source scores exactly 1.0.
WEIGHT_TITLE = 0.60
WEIGHT_CONTENT = 0.25
WEIGHT_SOURCE = 0.15

#: Title tiers.
TITLE_EXACT = 1.0  # the record IS the query ("Rani ki Vav")
TITLE_WORDS = 0.85  # query appears as whole words ("June 1946" for "1946")
TITLE_PART = 0.55  # query appears inside a longer word
#: Score for the *fraction* of query terms the title covers when the whole
#: query does not appear ("babbage mathematician" → "Charles Babbage"
#: covers 1 of 2 terms). Capped at TITLE_WORDS.
TITLE_TERM_COVERAGE = 0.8
TITLE_NONE = 0.0

#: Content tiers.
CONTENT_PHRASE = 1.0  # the whole query appears verbatim
CONTENT_ALL_TERMS = 0.6  # every term appears, non-adjacently
CONTENT_SOME_TERMS = 0.25
CONTENT_NONE = 0.0

#: Source tiers, decided by **provenance, not publisher name**: ids are
#: namespaced ``wiki:<pageid>`` for transient live hits, so every
#: persisted (curated) record counts as curated regardless of which
#: encyclopedia its summary came from — otherwise two curated records
#: would rank differently purely because one credits Britannica and the
#: other Wikipedia.
SOURCE_CURATED = 1.0
SOURCE_LIVE = 0.85
LIVE_RECORD_PREFIX = "wiki:"

#: Coarse label reported next to the score, so the UI never presents a
#: precision the ranking does not have.
RELEVANCE_HIGH = 0.75
RELEVANCE_MEDIUM = 0.45


def relevance_label(score: float | None) -> str | None:
    """``high`` / ``medium`` / ``low`` for a 0..1 retrieval score."""
    if score is None:
        return None
    if score >= RELEVANCE_HIGH:
        return "high"
    if score >= RELEVANCE_MEDIUM:
        return "medium"
    return "low"


def _title_tier(title: str, normalized: str, terms: Sequence[str]) -> float:
    """How well the query matches the record's title.

    Order: the title *is* the query, then whole-word containment, then
    substring, then the fraction of query terms the title covers (so
    "Charles Babbage" ranks above a record that merely mentions him).
    """
    if not normalized:
        return TITLE_NONE
    if title == normalized:
        return TITLE_EXACT
    if normalized in title:
        # Whole-word containment only: "1946" matches "June 1946" but a
        # fragment must not look like an exact subject match.
        if f" {normalized} " in f" {title} ":
            return TITLE_WORDS
        return TITLE_PART
    if terms:
        covered = sum(1 for term in terms if term in title)
        if covered:
            return round(min(TITLE_WORDS, TITLE_TERM_COVERAGE * covered / len(terms)), 4)
    return TITLE_NONE


def _content_tier(content: str, normalized: str, variants: Sequence[str], terms: Sequence[str]) -> float:
    """How well the query matches the record's text."""
    if normalized and normalized in content:
        return CONTENT_PHRASE
    if any(variant != normalized and variant in content for variant in variants):
        return CONTENT_PHRASE
    if terms and all(term in content for term in terms):
        return CONTENT_ALL_TERMS
    if terms and any(term in content for term in terms):
        return CONTENT_SOME_TERMS
    return CONTENT_NONE


def _score(
    record: HistoricalRecord,
    normalized: str,
    variants: Sequence[str],
    terms: Sequence[str],
) -> float:
    """Deterministic retrieval relevance in 0..1 (documented formula).

    ``score = 0.60 * title_tier + 0.25 * content_tier + 0.15 * source_tier``

    * ``title_tier`` — 1.0 exact title, 0.85 whole-word containment
      ("June 1946" for a query of "1946"), 0.55 substring, otherwise 0.8 ×
      the fraction of query terms the title covers;
    * ``content_tier`` — 1.0 the phrase (or a documented spelling variant)
      appears, 0.6 all terms appear non-adjacently, 0.25 some do, else 0;
    * ``source_tier`` — 1.0 a persisted (curated) record, 0.85 a transient
      live hit (``wiki:`` ids).

    The result describes **retrieval relevance only**. It says nothing
    about historical verification, evidence strength, or whether a claim
    is true — those are reported separately by Module C4's verification
    status. Replacing the old tier ladder is deliberate: that ladder gave
    every title containing the query the same score (every result for
    "1946" scored 0.9), which is exactly the misleading signal a user
    reads as confidence.

    ``terms`` are already filtered by :data:`MIN_TERM_LENGTH` (with a
    fallback to the raw terms when every token is short).
    """
    title = record.title.casefold()
    content = (record.content or "").casefold()
    is_live = record.id.startswith(LIVE_RECORD_PREFIX)
    source_tier = SOURCE_LIVE if is_live else SOURCE_CURATED

    score = (
        WEIGHT_TITLE * _title_tier(title, normalized, terms)
        + WEIGHT_CONTENT * _content_tier(content, normalized, variants, terms)
        + WEIGHT_SOURCE * source_tier
    )
    return round(min(1.0, score), 2)


def _build_excerpt(record: HistoricalRecord, candidates: Sequence[str]) -> str | None:
    """Window of the record's own text around the earliest candidate hit."""
    content = record.content or ""
    if not content:
        return None

    lowered = content.casefold()
    positions = [
        position
        for candidate in candidates
        if candidate
        for position in [lowered.find(candidate.casefold())]
        if position >= 0
    ]
    if positions:
        start = min(positions)
        end = min(len(content), start + len(content[start:].split(" ")[0]) + _EXCERPT_TAIL)
        # Snap to word boundaries and widen a little to the left.
        start = max(0, start - _EXCERPT_LEAD)
        if start > 0:
            next_space = content.find(" ", start)
            start = next_space + 1 if next_space != -1 else start
        if end < len(content):
            prev_space = content.rfind(" ", start, end)
            if prev_space > start:
                end = prev_space
        window = content[start:end].strip()
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(content) else ""
        return f"{prefix}{window}{suffix}"

    # Matched only in the title/source: fall back to the start of the text.
    head = content[:_FALLBACK_EXCERPT].strip()
    suffix = "…" if len(content) > _FALLBACK_EXCERPT else ""
    return f"{head}{suffix}"


async def search_historical_records(
    session: AsyncSession,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    stats: dict[str, int] | None = None,
) -> list[RetrievedRecord]:
    """Retrieve records relevant to ``query``, best match first.

    Returns ``[]`` for an empty/whitespace query and when nothing is
    relevant. ``limit``/``offset`` are applied after ranking.

    ``stats`` is an optional caller-owned counter map filled in by the live
    source (see :func:`app.integrations.wikipedia.search_wikipedia`), so the
    API can tell "the source had nothing" apart from "the source could not
    be asked". It never changes the returned records.
    """
    normalized = normalize_query(query)
    if not normalized:
        return []

    variants = spelling_variants(normalized)
    terms = normalized.split()
    # Significant terms drive both probing and scoring (see MIN_TERM_LENGTH).
    significant_terms = [term for term in terms if len(term) >= MIN_TERM_LENGTH]
    scoring_terms = significant_terms or terms

    # Query strings: full variants, plus individual terms when the query
    # has more than one (non-adjacent terms must still match). Term probes
    # are capped so a pathological query cannot fan out unboundedly, and
    # too-short tokens are skipped (MIN_TERM_LENGTH).
    probes = list(variants)
    if len(terms) > 1:
        probes.extend(
            dict.fromkeys(
                term
                for term in significant_terms[:MAX_TERM_PROBES]
                if term not in probes
            )
        )

    candidates: dict[str, HistoricalRecord] = {}
    for probe in probes:
        for record in await search_records(
            session, probe, limit=MAX_SEARCH_LIMIT, offset=0
        ):
            candidates.setdefault(record.id, record)

    # Opt-in live source (Repair R2): merge citable records for the real
    # query. Transient rows (never persisted), deduplicated by id; on any
    # provider problem the module returns [] and local search is intact.
    if live_sources_enabled():
        for hit in await search_wikipedia(
            query.strip(), limit=LIVE_SOURCE_LIMIT, stats=stats
        ):
            candidates.setdefault(
                hit.record_id,
                HistoricalRecord(
                    id=hit.record_id,
                    title=hit.title,
                    year=None,
                    source=LIVE_SOURCE_NAME,
                    url=hit.url,
                    content=hit.extract,
                ),
            )

    if not candidates:
        return []

    excerpt_probes = list(
        dict.fromkeys([normalized, *variants, *terms])
    )
    ranked = sorted(
        (
            RetrievedRecord(
                record=record,
                excerpt=_build_excerpt(record, excerpt_probes),
                score=_score(record, normalized, variants, scoring_terms),
            )
            for record in candidates.values()
        ),
        key=lambda hit: (-hit.score, hit.record.title.casefold(), hit.record.id),
    )

    bounded_limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    bounded_offset = max(0, int(offset))
    return ranked[bounded_offset : bounded_offset + bounded_limit]
