"""Grounded synthesis — the only place a model writes prose.

The briefing itself is extractive (quoted source sentences with citation
indexes). This service optionally asks the configured synthesis provider
(Adaption, see :mod:`app.integrations.adaption`) for **one short paragraph
over exactly those quoted statements**, and then checks the answer before
it is allowed anywhere near the UI:

* every sentence must carry a citation marker (``[1]``, ``[2]`` …);
* every cited number must be one of the statements' citation indexes;
* any 3-4 digit number in the text (years, counts) must appear in the
  statements — a model that invents "in 1199" fails this check;
* the text must be non-empty and within a length cap.

A failure is never papered over: the synthesis is dropped, the briefing
keeps its quoted explanation, and the reason is added to the briefing's
``uncertainty`` list so the reader sees that a generated summary was
requested and not shown.

No provider configured or no model available → this service returns
``None`` plus an explicit note; it never fabricates a paragraph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

import httpx

from ..core.logging import get_logger
from ..integrations import ProviderError
from ..integrations.adaption import (
    PROVIDER as ADAPTION_PROVIDER,
    AdaptionConfig,
    create_response,
    load_config,
    synthesis_enabled,
)
from ..schemas.search import BriefingSynthesis, HistoricalBriefing

logger = get_logger("services.synthesis")

__all__ = [
    "METHOD_SYNTHESIS",
    "SYNTHESIS_INSTRUCTIONS",
    "build_prompt",
    "statements_from_briefing",
    "synthesise_briefing",
    "validate_synthesis",
]

#: ``BriefingSynthesis.method`` — a model composed this text over quoted
#: statements, and every claim in it cites one of them.
METHOD_SYNTHESIS = "adaption-grounded-synthesis"

#: Hard ceiling so the summary can never become the page.
MAX_SYNTHESIS_CHARS = 1200
#: Statements handed to the model (bounded prompt, bounded cost).
MAX_STATEMENTS = 12

SYNTHESIS_INSTRUCTIONS = (
    "You summarise historical research notes. Use ONLY the numbered "
    "statements given by the user: they are quotations from cited sources. "
    "Write at most three sentences of plain prose. "
    "End every sentence with the citation markers of the statements it uses, "
    "like [1] or [1][3]. "
    "Never add a fact, date, name, number or place that is not in the "
    "statements. If the statements do not support a summary, reply exactly: "
    "INSUFFICIENT."
)

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_NUMBER_RE = re.compile(r"\b(\d{3,4})\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class SynthesisOutcome:
    """What came back, and what the reader must be told about it."""

    synthesis: Optional[BriefingSynthesis]
    notes: list[str]


def statements_from_briefing(briefing: HistoricalBriefing) -> list[tuple[int, str]]:
    """The cited statements a summary may be built from, in reading order.

    Drawn from the briefing's own quoted facts and dated events — the same
    material the page displays — so a summary can never see more than the
    reader does.
    """
    statements: list[tuple[int, str]] = []
    for fact in briefing.key_facts:
        statements.append((fact.source_index, fact.text))
    for entry in briefing.timeline:
        statements.append((entry.source_index, f"{entry.date} — {entry.description}"))
    deduped: list[tuple[int, str]] = []
    seen: set[str] = set()
    for index, text in statements:
        key = text.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((index, text.strip()))
    return deduped[:MAX_STATEMENTS]


def build_prompt(query: str, statements: Sequence[tuple[int, str]]) -> str:
    """The user turn: the query and the numbered source statements."""
    lines = [
        f"Historical subject: {query}",
        "Numbered statements (each quoted from a cited source):",
    ]
    for position, (source_index, text) in enumerate(statements, start=1):
        lines.append(f"[{source_index}] {text}")
    return "\n".join(lines)


def validate_synthesis(
    text: str, *, allowed_indexes: Sequence[int], statements: Sequence[tuple[int, str]]
) -> tuple[Optional[str], Optional[str]]:
    """``(clean_text, problem)`` — ``problem`` explains any rejection."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None, "The synthesis provider returned no text."
    if cleaned.upper().startswith("INSUFFICIENT"):
        return None, "The synthesis provider reported that the statements do not support a summary."
    if len(cleaned) > MAX_SYNTHESIS_CHARS:
        return None, "The synthesis provider returned more text than the summary contract allows."

    allowed = set(allowed_indexes)
    cited = [int(match) for match in _CITATION_RE.findall(cleaned)]
    if not cited:
        return None, "The synthesis provider returned a summary with no citations, so it was not shown."
    if any(index not in allowed for index in cited):
        return None, "The synthesis provider cited a source that is not in the results, so the summary was not shown."

    uncited = [
        sentence
        for sentence in _SENTENCE_SPLIT_RE.split(cleaned)
        if sentence.strip() and not _CITATION_RE.search(sentence)
    ]
    if uncited:
        return None, "The synthesis provider returned sentences without citations, so the summary was not shown."

    statement_text = " ".join(text for _, text in statements)
    statement_numbers = set(_NUMBER_RE.findall(statement_text))
    invented = [
        number
        for number in _NUMBER_RE.findall(cleaned)
        if number not in statement_numbers
    ]
    if invented:
        return None, (
            "The synthesis provider introduced numbers that are not in the "
            "retrieved statements, so the summary was not shown."
        )

    return cleaned, None


async def synthesise_briefing(
    briefing: HistoricalBriefing,
    *,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    config: Optional[AdaptionConfig] = None,
) -> SynthesisOutcome:
    """Ask the synthesis provider for a cited summary of the briefing.

    Returns the summary (validated) plus the notes the briefing must
    display. Never raises: every provider problem becomes a note, and the
    extractive briefing stays as it was.
    """
    if not synthesis_enabled(environ):
        return SynthesisOutcome(synthesis=None, notes=[])

    statements = statements_from_briefing(briefing)
    if not statements:
        return SynthesisOutcome(
            synthesis=None,
            notes=[
                "A generated summary was requested, but this briefing has no "
                "quoted statements to summarise."
            ],
        )

    try:
        resolved = config or load_config(environ)
    except ProviderError as exc:
        logger.warning("synthesis provider not configured: %s", exc)
        return SynthesisOutcome(
            synthesis=None,
            notes=[
                f"Generated summarisation is enabled but unavailable ({exc}); "
                "the explanation below is the retrieved sources' own wording."
            ],
        )

    allowed = sorted({index for index, _ in statements})
    try:
        raw = await create_response(
            build_prompt(briefing.query, statements),
            instructions=SYNTHESIS_INSTRUCTIONS,
            config=resolved,
            client=client,
            environ=environ,
        )
    except ProviderError as exc:
        logger.warning("synthesis provider failed: %s", exc)
        return SynthesisOutcome(
            synthesis=None,
            notes=[
                f"The summarisation provider failed ({type(exc).__name__}: {exc}); "
                "the explanation below is the retrieved sources' own wording."
            ],
        )

    cleaned, problem = validate_synthesis(
        raw, allowed_indexes=allowed, statements=statements
    )
    if cleaned is None:
        logger.warning("synthesis rejected: %s", problem)
        return SynthesisOutcome(
            synthesis=None,
            notes=[f"{problem} The explanation below is the retrieved sources' own wording."],
        )

    return SynthesisOutcome(
        synthesis=BriefingSynthesis(
            provider=ADAPTION_PROVIDER,
            model=resolved.model,
            method=METHOD_SYNTHESIS,
            text=cleaned,
            source_indexes=sorted({int(match) for match in _CITATION_RE.findall(cleaned)}),
        ),
        notes=[],
    )
