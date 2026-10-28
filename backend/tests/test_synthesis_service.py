"""Grounded synthesis — the guard rails around the generated paragraph.

Mocked transport; no provider call leaves the process. The point of these
tests is the *rejection* rules: a model that writes uncited prose, cites a
source that is not in the results, or invents a year must not have its
text shown, and the briefing must say why.
"""

from __future__ import annotations

import httpx
import pytest

from app.schemas.search import (
    BriefingSynthesis,
    HistoricalBriefing,
    KeyFact,
    TimelineEntry,
)
from app.services.synthesis_service import (
    METHOD_SYNTHESIS,
    build_prompt,
    statements_from_briefing,
    synthesise_briefing,
    validate_synthesis,
)

pytestmark = pytest.mark.anyio

ENV = {
    "HG_SYNTHESIS": "1",
    "ADAPTION_API_KEY": "pt_live_test",
    "ADAPTION_MODEL": "hosted-model-1",
}


def _briefing() -> HistoricalBriefing:
    return HistoricalBriefing(
        query="Rani ki Vav",
        query_kind="named",
        method="extractive-quotes",
        summary="Rani ki Vav is a stepwell in Patan, Gujarat.",
        explanation="Its construction is attributed to Udayamati.",
        key_facts=[
            KeyFact(text="Rani ki Vav is a stepwell in the town of Patan.", source_index=1),
            KeyFact(text="It was built in 1063.", source_index=1),
        ],
        timeline=[TimelineEntry(date="1063", description="Construction begins.", source_index=1)],
        sources=[],
    )


def _client(text: str, *, status: int = 200) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"output_text": text})
        )
    )


# ---------------------------------------------------------------------------
# Statements and prompt
# ---------------------------------------------------------------------------


def test_statements_come_from_the_briefings_own_quotes():
    statements = statements_from_briefing(_briefing())

    assert statements == [
        (1, "Rani ki Vav is a stepwell in the town of Patan."),
        (1, "It was built in 1063."),
        (1, "1063 — Construction begins."),
    ]


def test_prompt_numbers_the_statements_with_their_citation_indexes():
    prompt = build_prompt("Rani ki Vav", [(1, "A stepwell."), (2, "Built in 1063.")])

    assert "Historical subject: Rani ki Vav" in prompt
    assert "[1] A stepwell." in prompt
    assert "[2] Built in 1063." in prompt


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


def test_a_cited_summary_is_accepted():
    text, problem = validate_synthesis(
        "Rani ki Vav is a stepwell in Patan [1]. It was built in 1063 [1].",
        allowed_indexes=[1],
        statements=[(1, "Rani ki Vav is a stepwell in Patan."), (1, "It was built in 1063.")],
    )

    assert problem is None
    assert text is not None and text.endswith("[1].")


def test_uncited_prose_is_rejected():
    text, problem = validate_synthesis(
        "Rani ki Vav is famous [1]. It is the most beautiful stepwell in India.",
        allowed_indexes=[1],
        statements=[(1, "Rani ki Vav is a stepwell in Patan.")],
    )

    assert text is None
    assert problem is not None and "without citations" in problem


def test_a_citation_to_a_source_that_was_not_retrieved_is_rejected():
    text, problem = validate_synthesis(
        "A claim [4].", allowed_indexes=[1], statements=[(1, "A stepwell.")]
    )

    assert text is None
    assert problem is not None and "not in the results" in problem


def test_an_invented_date_is_rejected():
    text, problem = validate_synthesis(
        "It was built in 1199 [1].",
        allowed_indexes=[1],
        statements=[(1, "It was built in 1063.")],
    )

    assert text is None
    assert problem is not None and "numbers" in problem


def test_no_citations_at_all_is_rejected():
    text, problem = validate_synthesis(
        "A short paragraph.", allowed_indexes=[1], statements=[(1, "A stepwell.")]
    )

    assert text is None
    assert problem is not None


def test_insufficient_answer_is_rejected_with_its_own_message():
    text, problem = validate_synthesis(
        "INSUFFICIENT", allowed_indexes=[1], statements=[(1, "A stepwell.")]
    )

    assert text is None
    assert problem is not None and "do not support a summary" in problem


# ---------------------------------------------------------------------------
# Service behaviour
# ---------------------------------------------------------------------------


async def test_synthesis_is_off_by_default_and_calls_nothing():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"output_text": "should not happen [1]."})

    outcome = await synthesise_briefing(
        _briefing(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), environ={}
    )

    assert outcome.synthesis is None
    assert outcome.notes == []
    assert calls == []


async def test_a_valid_generated_summary_is_returned_with_its_citations():
    outcome = await synthesise_briefing(
        _briefing(),
        client=_client("Rani ki Vav is a stepwell in Patan [1]. It was built in 1063 [1]."),
        environ=ENV,
    )

    synthesis = outcome.synthesis
    assert synthesis is not None
    assert isinstance(synthesis, BriefingSynthesis)
    assert synthesis.method == METHOD_SYNTHESIS
    assert synthesis.provider == "Adaption"
    assert synthesis.model == "hosted-model-1"
    assert synthesis.source_indexes == [1]
    assert outcome.notes == []


async def test_a_rejected_summary_is_dropped_and_explained():
    outcome = await synthesise_briefing(
        _briefing(), client=_client("It was built in 1199 [1]."), environ=ENV
    )

    assert outcome.synthesis is None
    assert outcome.notes and "not shown" in outcome.notes[0]


async def test_provider_failure_leaves_the_quoted_briefing_intact():
    outcome = await synthesise_briefing(
        _briefing(), client=_client("boom", status=500), environ=ENV
    )

    assert outcome.synthesis is None
    assert outcome.notes and "ProviderServerError" in outcome.notes[0]
    assert "retrieved sources' own wording" in outcome.notes[0]


async def test_model_unavailable_is_reported_as_such():
    """The verified state of this deployment: credentials work, no model."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "Model not found"}})

    outcome = await synthesise_briefing(
        _briefing(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), environ=ENV
    )

    assert outcome.synthesis is None
    assert outcome.notes and "AdaptionModelUnavailableError" in outcome.notes[0]
    # The message a user sees names the variable, never the key value.
    assert "pt_live_test" not in outcome.notes[0]


async def test_enabled_without_configuration_says_what_is_missing():
    outcome = await synthesise_briefing(_briefing(), environ={"HG_SYNTHESIS": "1"})

    assert outcome.synthesis is None
    assert outcome.notes and "ADAPTION_API_KEY" in outcome.notes[0]


async def test_briefing_without_quoted_statements_is_not_summarised():
    empty = HistoricalBriefing(
        query="Unknown", query_kind="general", method="extractive-quotes"
    )

    outcome = await synthesise_briefing(empty, environ=ENV)

    assert outcome.synthesis is None
    assert outcome.notes and "no quoted statements" in outcome.notes[0]
