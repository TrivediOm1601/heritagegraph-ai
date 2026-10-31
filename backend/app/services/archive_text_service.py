"""Text intelligence for archived documents: dates and entity candidates.

Both extractors are deliberately **conservative and labelled**:

* dates are read from the document's own wording and keep that wording
  (``"c. 1020"``, ``"11th century"``, ``"3rd century BCE"``); only explicit
  numbers become numeric bounds, and a century or approximate date is never
  flattened into a false exact year;
* entity candidates are a gazetteer match against the app's existing records
  (high precision) plus a capitalisation heuristic (low confidence, type
  ``other``). They are stored as ``candidate`` evidence with the sentence
  they came from — the archive never claims an extracted name is a verified
  historical fact.

No model is called here. This runs on the extracted text alone, so it works
offline, cannot fabricate a citation, and is deterministic in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

__all__ = [
    "DateMention",
    "EntityMention",
    "parse_date_mentions",
    "extract_entity_mentions",
    "MAX_TIMELINE_EVENTS",
    "MAX_ENTITY_CANDIDATES",
]

#: Per-file caps: a research document must not become an unbounded timeline.
MAX_TIMELINE_EVENTS = 120
MAX_ENTITY_CANDIDATES = 150

#: Words that start a sentence or are too generic to be a topic on their own.
_STOPWORDS = frozenset(
    """
    a an the this that these those it its he she they them his her their we our you
    i in on at by for from with without into over under about after before during
    between among through of to and or but not no nor so as is are was were be been
    being has have had do does did will would can could may might must shall should
    chapter section figure table plate page vol volume part appendix note notes
    source sources archive archives collection collections document documents
    photograph photographs image images map maps report reports india indian gujarat
    gujarati heritage historical history century year years month day
    """.split()
)

_MONTHS = (
    "January February March April May June July August September October November "
    "December"
).split()

_RANGE = re.compile(
    r"\b(?:(?:between|from)\s+)?(\d{3,4})\s*(BCE|BC|CE|AD)?\s*"
    r"(?:and|to|[-–—])\s*(\d{3,4})\s*(BCE|BC|CE|AD)\b",
    re.IGNORECASE,
)
_CENTURY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)\s+century(?:\s*(BCE|BC|CE|AD))?\b",
    re.IGNORECASE,
)
_APPROX = re.compile(
    r"\b(?:c\.|ca\.|circa|approx\.?|around|about)\s*(\d{3,4})\s*(BCE|BC|CE|AD)?\b",
    re.IGNORECASE,
)
_EXPLICIT = re.compile(r"\b(\d{3,4})\s*(BCE|BC|CE|AD)\b", re.IGNORECASE)
_CONTEXT_YEAR = re.compile(
    r"\b(?:in|from|by|dated|built|constructed|founded|established|completed|since|"
    r"until|reign|reigned|died|born)\s+(?:the year\s+)?(\d{3,4})\b",
    re.IGNORECASE,
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n{1,}")


@dataclass(frozen=True)
class DateMention:
    """One date as written in the text, plus its parsed extent."""

    date_text: str
    year_start: Optional[int]
    year_end: Optional[int]
    era: str  # 'ce' | 'bce' | 'unknown'
    precision: str  # exact | approximate | century | range
    sentence: str


@dataclass(frozen=True)
class EntityMention:
    """One candidate entity with the passage that supports it."""

    name: str
    entity_type: str  # canonical ENTITY_TYPES only
    confidence: float
    page_number: Optional[int]
    excerpt: str
    method: str  # 'gazetteer' | 'heuristic' | 'date'


def _signed_year(number: int, era: str) -> int:
    return -number if era == "bce" else number


def _century_bounds(number: int, era: str) -> tuple[int, int]:
    if era == "bce":
        return -(number * 100), -((number - 1) * 100 + 1)
    return (number - 1) * 100 + 1, number * 100


def _sentence_around(text: str, start: int, end: int, *, limit: int = 320) -> str:
    """The sentence containing ``text[start:end]``, trimmed to ``limit``."""
    left = 0
    for match in re.finditer(r"[.!?](?:\s|$)|\n", text[:start]):
        left = match.end()
    right_match = re.search(r"[.!?](?:\s|$)|\n", text[end:])
    right = end + right_match.end() if right_match else len(text)
    sentence = " ".join(text[left:right].split())
    if len(sentence) > limit:
        # Keep the date visible: trim the tail, not the beginning.
        sentence = sentence[: limit - 1].rstrip(" ,;:") + "…"
    return sentence


def _era_of(markers: list[str], *, allow_ce_default: bool) -> str:
    """Era from explicit markers; CE only when the number itself proves it."""
    if markers:
        return "bce" if markers[-1].upper() in {"BC", "BCE"} else "ce"
    return "ce" if allow_ce_default else "unknown"


def parse_date_mentions(text: str) -> list[DateMention]:
    """Extract dated passages, deduplicated and ordered chronologically.

    The most specific pattern wins: a range is not also reported as two
    separate years, ``"c. 1020"`` is not reported as exact, and a century
    without a marker keeps ``era='unknown'`` rather than guessing CE.
    """
    if not text.strip():
        return []

    collected: list[DateMention] = []
    claimed: list[tuple[int, int]] = []

    def overlaps(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= start or span[0] >= end) for start, end in claimed)

    def add(span: tuple[int, int], mention: DateMention) -> None:
        if overlaps(span):
            return
        claimed.append(span)
        collected.append(mention)

    def build(match: re.Match[str], *, era: str, start: int, end: int, precision: str) -> DateMention:
        return DateMention(
            date_text=" ".join(match.group(0).split()),
            year_start=start,
            year_end=end,
            era=era,
            precision=precision,
            sentence=_sentence_around(text, match.start(), match.end()),
        )

    # 1. Ranges: "1000–1025 CE", "between 250 and 200 BCE".
    for match in _RANGE.finditer(text):
        numbers = [group for group in match.groups() if group and group.isdigit()]
        if len(numbers) < 2:
            continue
        markers = [
            group
            for group in match.groups()
            if group and group.upper() in {"BC", "BCE", "CE", "AD"}
        ]
        era = _era_of(markers, allow_ce_default=True)
        start = _signed_year(int(numbers[0]), era)
        end = _signed_year(int(numbers[1]), era)
        if start > end:
            start, end = end, start
        add(match.span(), build(match, era=era, start=start, end=end, precision="range"))

    # 2. Approximate years: "c. 1020", "circa 300 BCE".
    for match in _APPROX.finditer(text):
        number = int(match.group(1))
        marker = match.group(2)
        era = _era_of([marker] if marker else [], allow_ce_default=number >= 1000)
        year = _signed_year(number, era)
        add(
            match.span(),
            build(match, era=era, start=year, end=year, precision="approximate"),
        )

    # 3. Centuries: "11th century CE" → 1001–1100 CE; "3rd century BCE" → −300…−201.
    for match in _CENTURY.finditer(text):
        number = int(match.group(1))
        marker = match.group(2)
        era = _era_of([marker] if marker else [], allow_ce_default=False)
        start, end = _century_bounds(number, era if era != "unknown" else "ce")
        add(
            match.span(),
            build(match, era=era, start=start, end=end, precision="century"),
        )

    # 4. Explicitly marked single years: "1025 CE", "250 BCE".
    for match in _EXPLICIT.finditer(text):
        number = int(match.group(1))
        era = _era_of([match.group(2)], allow_ce_default=False)
        year = _signed_year(number, era)
        add(match.span(), build(match, era=era, start=year, end=year, precision="exact"))

    # 5. Bare years, only where a construction/establishment verb dates them.
    for match in _CONTEXT_YEAR.finditer(text):
        year = int(match.group(1))
        if not 500 <= year <= 2100:
            continue
        add(
            match.span(),
            build(match, era="ce", start=year, end=year, precision="exact"),
        )

    # Deduplicate identical wording inside the same sentence.
    unique: list[DateMention] = []
    seen: set[tuple[str, str]] = set()
    for mention in collected:
        key = (mention.date_text.casefold(), mention.sentence.casefold())
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)

    unique.sort(
        key=lambda mention: (
            mention.year_start if mention.year_start is not None else 10**9,
            mention.date_text,
        )
    )
    return unique[:MAX_TIMELINE_EVENTS]


#: Title-case phrase candidate (2-4 capitalised words, optional connectives).
_PHRASE = re.compile(
    r"\b([A-Z][A-Za-z'’\-]{1,}(?:\s+(?:(?:of|the|and|de|al|bin|bint|ki|ka|"
    r"Singh|Shah|Khan|Kumari|Devi)\s+)?[A-Z][A-Za-z'’\-]{1,}){0,3})\b"
)


def _acceptable_phrase(phrase: str) -> bool:
    words = phrase.split()
    if not words or len(phrase) < 3:
        return False
    if words[0].casefold() in _STOPWORDS:
        return False
    if phrase.casefold() in _STOPWORDS:
        return False
    if all(word.isupper() for word in words) and len(phrase) <= 4:
        return False  # "ASI", "UNESCO" style acronyms are not topics
    if any(word.casefold() in _MONTHS for word in words):
        return False
    return True


def extract_entity_mentions(
    pages: Sequence[tuple[Optional[int], str]],
    known_names: Optional[dict[str, tuple[str, str]]] = None,
    *,
    max_per_page: int = 25,
) -> list[EntityMention]:
    """Candidate entities from page text.

    ``known_names`` maps a normalised name to ``(display_name, entity_type)``
    — the app's existing canonical entities and curated record titles. Those
    matches are high confidence (0.9) because the name is already an
    established entity; capitalised phrases are 0.45 and typed ``other``.
    """
    known = known_names or {}
    # Longest first, so "Taj Mahal" wins over "Taj".
    ordered = sorted(known.items(), key=lambda item: len(item[0]), reverse=True)
    compiled = [
        (normalised, re.compile(rf"(?<![\w'’])({re.escape(name)})(?![\w'’])", re.IGNORECASE))
        for normalised, name in ((key, known[key][0]) for key in sorted(known, key=len, reverse=True))
        if name.strip()
    ]

    results: dict[tuple[str, str], EntityMention] = {}

    def add(mention: EntityMention) -> None:
        key = (mention.name.casefold(), mention.entity_type)
        if key not in results:
            results[key] = mention

    for page_number, text in pages:
        if not text:
            continue
        claimed: list[tuple[int, int]] = []

        for normalised, pattern in compiled:
            for match in pattern.finditer(text):
                display, entity_type = known[normalised]
                claimed.append(match.span())
                add(
                    EntityMention(
                        name=display,
                        entity_type=entity_type,
                        confidence=0.9,
                        page_number=page_number,
                        excerpt=_sentence_around(text, match.start(), match.end()),
                        method="gazetteer",
                    )
                )

        # Dates as entities, from the same parser the timeline uses.
        for date in parse_date_mentions(text):
            add(
                EntityMention(
                    name=date.date_text,
                    entity_type="date",
                    confidence=0.8,
                    page_number=page_number,
                    excerpt=date.sentence,
                    method="date",
                )
            )

        # Capitalisation heuristic: low confidence, never more than the cap.
        if len(claimed) >= max_per_page:
            continue
        for match in _PHRASE.finditer(text):
            if any(not (match.span()[1] <= start or match.span()[0] >= end) for start, end in claimed):
                continue
            phrase = " ".join(match.group(1).split())
            if not _acceptable_phrase(phrase):
                continue
            add(
                EntityMention(
                    name=phrase,
                    entity_type="other",
                    confidence=0.45,
                    page_number=page_number,
                    excerpt=_sentence_around(text, match.start(), match.end()),
                    method="heuristic",
                )
            )
            claimed.append(match.span())
            if len(claimed) >= max_per_page:
                break

    ordered_results = sorted(
        results.values(), key=lambda item: (-item.confidence, item.name.casefold())
    )
    return ordered_results[:MAX_ENTITY_CANDIDATES]
