"""Search resilience — the first attempt must finish, and finish with results.

Two latency faults made a *successful* first search look like a failure:
the briefing probed its candidate article titles one after another (each
with its own timeout), and nothing bounded the enrichment as a whole, so a
slow source could outlive the browser's 30s search timeout. The retry then
succeeded only because the first attempt had warmed a cache.

These tests pin the fixed behaviour down without any network:

* candidate probes run **concurrently** (a barrier only all-in-flight calls
  can open proves it deterministically, no timing assertion);
* a briefing that hangs past its budget is dropped while the **retrieved
  results are still returned**, with a notice that says so.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.routes import search as search_routes
from app.db.models import HistoricalRecord
from app.services import briefing_service
from app.services.retrieval_service import RetrievedRecord

pytestmark = pytest.mark.anyio

LIVE_ENV = {"HG_LIVE_SOURCES": "1"}


def _hit(title: str) -> RetrievedRecord:
    return RetrievedRecord(
        record=HistoricalRecord(
            id=f"wiki:{title}",
            title=title,
            year=None,
            source="Wikipedia",
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            content=f"{title} is a historical subject.",
        ),
        excerpt=f"{title} is a historical subject.",
        score=0.9,
    )


async def test_candidate_page_probes_run_concurrently(monkeypatch):
    """All three probes must be in flight at once.

    The barrier opens only when every probe has started, so a sequential
    implementation can never reach it (and the first probe times out).
    """
    started = 0
    all_started = asyncio.Event()

    async def fake_fetch_page_text(title: str, **_: Any):
        nonlocal started
        started += 1
        if started >= 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1.0)
        return None  # no page: the probe simply fails over

    monkeypatch.setattr(briefing_service, "fetch_page_text", fake_fetch_page_text)

    # Three distinct titles are probed: the query plus the top two hits.
    hits = [_hit("Shah Jahan"), _hit("Mughal Empire"), _hit("Agra")]
    primary, _notes = await briefing_service._resolve_primary(
        "Taj Mahal", hits, live=True, client=None, environ=LIVE_ENV
    )

    assert started == 3
    # Nothing answered, so it falls back to the stored record text exactly
    # as before (the best-scoring hit, not the query string).
    assert primary.title == "Shah Jahan"


async def test_a_hanging_briefing_does_not_stall_the_search(monkeypatch):
    """The retrieval is served even when the explanation never answers."""

    class _Session:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *_: Any) -> None:
            return None

    async def fake_retrieval(session: Any, query: str, *, stats: dict | None = None):
        if stats is not None:
            stats["ok"] = 1
        return [_hit("Taj Mahal")]

    async def hanging_briefing(*_: Any, **__: Any) -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(search_routes, "session_scope", lambda: _Session())
    monkeypatch.setattr(search_routes, "search_historical_records", fake_retrieval)
    monkeypatch.setattr(search_routes, "build_briefing", hanging_briefing)
    # A tiny budget keeps the test instant; the real one is asserted below.
    monkeypatch.setattr(search_routes, "BRIEFING_BUDGET_SECONDS", 0.05)

    outcome = await search_routes._run_search("Taj Mahal")

    # The results are what the user asked for; only the explanation is lost.
    assert [hit.record.title for hit in outcome.hits] == ["Taj Mahal"]
    assert outcome.briefing is None
    assert outcome.notice == search_routes.BRIEFING_TIMEOUT_NOTICE


async def test_the_budget_is_short_enough_for_the_browser_timeout():
    """Retrieval + briefing must fit inside the client's 30s search timeout."""
    frontend_timeout_seconds = 30.0
    # Worst case: the live search spends its whole retry budget, then the
    # briefing spends its own.
    from app.integrations.retry import DEFAULT_POLICY

    worst_case = DEFAULT_POLICY.max_total_seconds + search_routes.BRIEFING_BUDGET_SECONDS
    assert worst_case < frontend_timeout_seconds
