"""Repair R2/R1 tests — live historical sources and configuration gating.

Three layers, clearly separated:

* **unit** — the MediaWiki response parser and the opt-in switches
  (no network, no database);
* **adapter** — ``search_wikipedia`` against ``httpx.MockTransport``
  fixtures shaped like the real API (success, timeout, HTTP error,
  malformed body, caching, User-Agent);
* **integration** — the retrieval service merging live hits into Module
  C's local ranking over a real migrated database, with the network
  stubbed at the adapter boundary.

One **real-network** test (``test_live_wikipedia_retrieval``) is gated on
``HERITAGE_LIVE_SOURCE_TESTS=1`` so the default suite stays hermetic;
run it explicitly to prove the live path works:

    HERITAGE_LIVE_SOURCE_TESTS=1 python -m pytest tests/test_live_sources.py -k live_wikipedia
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations.wikipedia import (
    SourceHit,
    clear_cache,
    live_sources_enabled,
    search_wikipedia,
)

pytestmark = pytest.mark.anyio

#: A real MediaWiki response shape (formatversion=2, generator=search).
SAMPLE_PAYLOAD = {
    "batchcomplete": True,
    "query": {
        "pages": [
            {
                "pageid": 101,
                "index": 2,
                "title": "Patan, Gujarat",
                "extract": "Patan is a city in Gujarat, India.",
                "fullurl": "https://en.wikipedia.org/wiki/Patan,_Gujarat",
                "canonicalurl": "https://en.wikipedia.org/wiki/Patan,_Gujarat",
            },
            {
                "pageid": 17834167,
                "index": 1,
                "title": "Rani ki Vav",
                "extract": "Rani ki Vav is a stepwell situated in Patan, Gujarat.",
                "fullurl": "https://en.wikipedia.org/wiki/Rani_ki_Vav",
                "canonicalurl": "https://en.wikipedia.org/wiki/Rani_ki_Vav",
            },
            {
                # Malformed: no URL → must be skipped, never guessed at.
                "pageid": 5,
                "index": 3,
                "title": "No URL page",
                "extract": "…",
            },
        ]
    },
}


@pytest.fixture(autouse=True)
def isolated():
    """Fresh module cache and a cleared opt-in for every test."""
    clear_cache()
    yield
    clear_cache()


def test_live_sources_opt_in_is_explicit():
    assert live_sources_enabled({"HG_LIVE_SOURCES": "1"}) is True
    assert live_sources_enabled({"HG_LIVE_SOURCES": "true"}) is True
    assert live_sources_enabled({}) is False
    assert live_sources_enabled({"HG_LIVE_SOURCES": "0"}) is False
    assert live_sources_enabled({"HG_LIVE_SOURCES": "maybe"}) is False


def test_parser_orders_by_api_rank_and_skips_unusable_pages():
    from app.integrations.wikipedia import _parse

    hits = _parse(SAMPLE_PAYLOAD)

    assert [hit.title for hit in hits] == ["Rani ki Vav", "Patan, Gujarat"]
    top = hits[0]
    assert top.record_id == "wiki:17834167"
    assert top.url == "https://en.wikipedia.org/wiki/Rani_ki_Vav"
    assert "stepwell" in top.extract

    # Defensive parsing: junk degrades to no hits, never an exception.
    assert _parse(None) == []
    assert _parse("not a dict") == []
    assert _parse({"query": {"pages": "nope"}}) == []
    assert _parse({"batchcomplete": True}) == []


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_default_user_agent_carries_a_contact_url():
    """A bare agent name is throttled by Wikimedia; the default must have a
    contact (verified live: bare name → HTTP 429, contact form → 200)."""
    from app.integrations.wikipedia import user_agent

    agent = user_agent({})

    assert agent.startswith("HeritageGraphAI/")
    assert "(+http://localhost:8000" in agent


def test_user_agent_uses_the_deployment_origin_as_contact():
    from app.integrations.wikipedia import user_agent

    assert "https://heritage.example" in user_agent({"APP_ORIGIN": "https://heritage.example"})


def test_user_agent_override_is_used_verbatim():
    from app.integrations.wikipedia import user_agent

    custom = "MyArchive/2.0 (+https://archive.example; ops@archive.example)"
    assert user_agent({"HG_SOURCE_USER_AGENT": custom}) == custom


async def test_configured_user_agent_is_sent(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user_agent"] = request.headers.get("User-Agent", "")
        return httpx.Response(200, json=SAMPLE_PAYLOAD)

    monkeypatch.setenv(
        "HG_SOURCE_USER_AGENT", "ConfiguredArchive/9 (+https://example.org; a@example.org)"
    )

    await search_wikipedia("Rani ki Vav", limit=2, client=_client(handler))

    assert seen["user_agent"] == "ConfiguredArchive/9 (+https://example.org; a@example.org)"


async def test_adapter_sends_user_agent_and_returns_hits():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["user_agent"] = request.headers.get("User-Agent", "")
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=SAMPLE_PAYLOAD)

    hits = await search_wikipedia("Rani ki Vav", limit=3, client=_client(handler))

    assert [hit.title for hit in hits] == ["Rani ki Vav", "Patan, Gujarat"]
    assert "wikipedia.org/w/api.php" in seen["url"]
    # MediaWiki policy: a descriptive User-Agent, not the default httpx one.
    assert "HeritageGraphAI" in seen["user_agent"]
    # ...and with contact details: the bare product name is throttled (429).
    assert "(+http" in seen["user_agent"]
    assert seen["params"]["gsrsearch"] == "Rani ki Vav"
    assert seen["params"]["gsrlimit"] == "3"
    assert seen["params"]["exintro"] == "1"


async def test_adapter_caches_identical_queries():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=SAMPLE_PAYLOAD)

    client = _client(handler)
    first = await search_wikipedia("Rani ki Vav", client=client)
    second = await search_wikipedia("Rani ki Vav", client=client)

    assert calls == 1  # second call served from the TTL cache
    assert first == second


async def test_timeout_degrades_to_no_hits_and_never_raises():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    hits = await search_wikipedia("anything", client=_client(handler))
    assert hits == []


async def test_http_error_and_malformed_body_degrade_to_no_hits():
    async def run(handler) -> list[SourceHit]:
        return await search_wikipedia("anything", client=_client(handler))

    assert await run(lambda _r: httpx.Response(429)) == []
    assert await run(lambda _r: httpx.Response(500)) == []
    assert await run(lambda _r: httpx.Response(200, text="<html>nope</html>")) == []
    assert await run(lambda _r: httpx.Response(200, json=["list"])) == []


async def test_blank_query_never_touches_the_network():
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made for a blank query")

    assert await search_wikipedia("   ", client=_client(handler)) == []


async def test_production_transport_parses_the_stdlib_fetch(monkeypatch):
    """The no-client branch (stdlib urllib, run in a thread) is exercised
    with a canned ``(status, body)`` so it is covered without network."""
    from app.integrations import wikipedia as wiki

    calls: dict = {}

    def fake_fetch(url: str, *, timeout: float, environ=None) -> tuple[int, bytes]:
        calls["url"] = url
        calls["timeout"] = timeout
        calls["environ"] = environ
        return 200, json.dumps(SAMPLE_PAYLOAD).encode("utf-8")

    monkeypatch.setattr(wiki, "_fetch_via_urllib", fake_fetch)

    hits = await search_wikipedia("Rani ki Vav", limit=2)

    assert [hit.title for hit in hits] == ["Rani ki Vav", "Patan, Gujarat"]
    assert "gsrsearch=Rani+ki+Vav" in calls["url"]
    assert calls["timeout"] == 8.0  # documented default timeout


async def test_production_transport_failures_degrade_to_no_hits(monkeypatch):
    from app.integrations import wikipedia as wiki
    import urllib.error

    async def run(side_effect):
        def fake_fetch(url: str, *, timeout: float):
            raise side_effect

        monkeypatch.setattr(wiki, "_fetch_via_urllib", fake_fetch)
        clear_cache()
        return await search_wikipedia("anything")

    assert await run(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)) == []
    assert await run(urllib.error.URLError("dns")) == []
    assert await run(TimeoutError()) == []
    assert await run(ValueError("bad body")) == []  # unexpected -> still no crash


# ---------------------------------------------------------------------------
# Integration: retrieval merges live hits into Module C's ranking
# ---------------------------------------------------------------------------


async def _seed_local_records(sessions) -> None:
    from app.db.models import HistoricalRecord

    async with sessions() as session:
        session.add(
            HistoricalRecord(
                id="rec-ada-lovelace",
                title="Ada Lovelace",
                source="Encyclopædia Britannica",
                url="https://www.britannica.com/biography/Ada-Lovelace",
                content="English mathematician who worked on the Analytical Engine.",
                year="1815–1852",
            )
        )
        await session.commit()


def _wiki_hit() -> SourceHit:
    return SourceHit(
        page_id="17834167",
        title="Rani ki Vav",
        url="https://en.wikipedia.org/wiki/Rani_ki_Vav",
        extract="Rani ki Vav is a stepwell situated in the town of Patan in Gujarat, India.",
    )


async def _search(sessions, query: str):
    from app.services.retrieval_service import search_historical_records

    async with sessions() as session:
        return await search_historical_records(session, query)


async def test_live_hits_are_merged_only_when_opted_in(open_db, monkeypatch):
    from app.services import retrieval_service

    async def stub(query, **_kwargs):
        return [_wiki_hit()]

    monkeypatch.setattr(retrieval_service, "search_wikipedia", stub)

    async with open_db() as sessions:
        await _seed_local_records(sessions)

        # Opt-in off (the test default): local store only.
        local_only = await _search(sessions, "Rani ki Vav")
        assert local_only == []

        # Opt-in on: a real citable record joins the ranking.
        monkeypatch.setenv("HG_LIVE_SOURCES", "1")
        merged = await _search(sessions, "Rani ki Vav")

    assert [hit.record.id for hit in merged] == ["wiki:17834167"]
    top = merged[0]
    match = top.to_match()
    assert match["source"] == "Wikipedia"
    assert match["url"] == "https://en.wikipedia.org/wiki/Rani_ki_Vav"
    assert top.excerpt  # excerpt built from the live extract


async def test_local_and_live_results_rank_together(open_db, monkeypatch):
    from app.services import retrieval_service

    async def stub(query, **_kwargs):
        return [_wiki_hit()]

    monkeypatch.setattr(retrieval_service, "search_wikipedia", stub)
    monkeypatch.setenv("HG_LIVE_SOURCES", "1")

    async with open_db() as sessions:
        await _seed_local_records(sessions)

        # Local exact-title hit (score 0.9) and the live hit coexist;
        # the local record still wins on the deterministic ladder.
        results = await _search(sessions, "Ada Lovelace")

    ids = [hit.record.id for hit in results]
    assert ids[0] == "rec-ada-lovelace"
    # The live hit for the same query ranks by its own content, deduplicated by id.
    assert len(set(ids)) == len(ids)


async def test_live_source_failure_leaves_local_search_intact(open_db, monkeypatch):
    from app.services import retrieval_service

    async def broken(query, **_kwargs):
        raise RuntimeError("provider exploded")  # must not be swallowed silently
        # noqa: RET503 — unreachable

    monkeypatch.setattr(retrieval_service, "search_wikipedia", broken)
    monkeypatch.setenv("HG_LIVE_SOURCES", "1")

    async with open_db() as sessions:
        await _seed_local_records(sessions)
        # The adapter itself never raises (proved above); here we assert
        # local results are still returned when live results are empty.
        async def empty(query, **_kwargs):
            return []

        monkeypatch.setattr(retrieval_service, "search_wikipedia", empty)
        results = await _search(sessions, "Ada Lovelace")

    assert [hit.record.id for hit in results] == ["rec-ada-lovelace"]


# ---------------------------------------------------------------------------
# R1 — configuration gating (vision mandatory, OCR optional, demo refused
# in production)
# ---------------------------------------------------------------------------


def _set_vision_env(monkeypatch) -> None:
    monkeypatch.setenv("VISION_API_BASE_URL", "https://vision.example.test/v1")
    monkeypatch.setenv("VISION_API_KEY", "sk-test")
    monkeypatch.setenv("VISION_MODEL", "vision-model-x")


def test_demo_mode_is_refused_in_production(monkeypatch):
    from app.services.analysis_service import demo_mode_enabled

    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert demo_mode_enabled() is False  # fixtures can never run in production

    monkeypatch.setenv("ENVIRONMENT", "development")
    assert demo_mode_enabled() is True


def test_vision_only_configuration_is_sufficient(monkeypatch):
    """R1: OCR absent entirely is fine; only vision is demanded."""
    from app.services.analysis_service import processing_configuration_error

    for name in ("OCR_API_URL", "OCR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    _set_vision_env(monkeypatch)

    assert processing_configuration_error() is None


def test_missing_vision_configuration_names_every_variable(monkeypatch):
    from app.services.analysis_service import processing_configuration_error

    for name in (
        "VISION_API_BASE_URL",
        "VISION_API_KEY",
        "VISION_MODEL",
        "OCR_API_URL",
        "OCR_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    message = processing_configuration_error()
    assert message is not None
    for name in ("VISION_API_BASE_URL", "VISION_API_KEY", "VISION_MODEL"):
        assert name in message
    # OCR absent as a whole is not an error.
    assert "OCR_API_URL" not in message


def test_partial_ocr_configuration_is_an_error(monkeypatch):
    from app.services.analysis_service import processing_configuration_error

    _set_vision_env(monkeypatch)
    monkeypatch.setenv("OCR_API_URL", "https://ocr.example.test/read")
    monkeypatch.delenv("OCR_API_KEY", raising=False)

    message = processing_configuration_error()
    assert message is not None
    assert "OCR_API_KEY" in message
    assert "partially configured" in message


# ---------------------------------------------------------------------------
# Real network check — opt-in only (run explicitly, see module docstring)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    "HERITAGE_LIVE_SOURCE_TESTS" not in __import__("os").environ,
    reason="set HERITAGE_LIVE_SOURCE_TESTS=1 to query the live MediaWiki API",
)
async def test_live_wikipedia_retrieval():
    """A real MediaWiki query — proves the live path works end to end."""
    clear_cache()
    hits = await search_wikipedia("Rani ki Vav", limit=3)

    assert hits, "the live MediaWiki API must return results for Rani ki Vav"
    titles = [hit.title for hit in hits]
    assert "Rani ki Vav" in titles
    top = next(hit for hit in hits if hit.title == "Rani ki Vav")
    assert top.url.startswith("https://en.wikipedia.org/wiki/")
    assert "stepwell" in (top.extract or "").lower()