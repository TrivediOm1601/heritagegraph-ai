"""Reliability tests — bounded retries and failure caching for live sources.

The bug these pin down: a live source answering **429** (or a transient
5xx / timeout) on the *first* attempt used to be reported as "no results",
so the user's first search came back thin and simply repeating it worked.
The second-order bug was caching: a transient failure was remembered as
"this page has no text" / "this subject has no photograph" for the whole
TTL, so the retry could not recover either.

Layers, deliberately separated:

* **policy** — pure functions (which statuses retry, ``Retry-After``
  parsing, backoff bounds, env clamping);
* **runner** — the retry loop itself, with an injected sleep so these
  tests never wait;
* **adapters** — ``search_wikipedia`` / ``fetch_page_text`` /
  ``fetch_page_media`` / ``fetch_subject_classes`` against
  ``httpx.MockTransport``: zero egress, no credentials, no live provider.

No test here proves the live provider works — that requires a real
network call and is covered (opt-in) by ``test_live_sources.py``.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
import pytest

from app.integrations import retry as retry_module
from app.integrations.retry import (
    DEFAULT_POLICY,
    RetryPolicy,
    TransientSourceError,
    backoff_delay,
    is_retryable_status,
    parse_retry_after,
    policy_from_environ,
    run_with_retries,
)
from app.integrations.wikidata import clear_cache as clear_wikidata_cache
from app.integrations.wikidata import fetch_subject_classes
from app.integrations.wikimedia_media import clear_cache as clear_media_cache
from app.integrations.wikimedia_media import fetch_page_media
from app.integrations.wikipedia import clear_cache as clear_wiki_cache
from app.integrations.wikipedia import fetch_page_text, search_wikipedia

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clear_caches():
    clear_wiki_cache()
    clear_media_cache()
    clear_wikidata_cache()
    yield
    clear_wiki_cache()
    clear_media_cache()
    clear_wikidata_cache()


# ---------------------------------------------------------------------------
# Fixture payloads (real API shapes)
# ---------------------------------------------------------------------------

SEARCH_PAYLOAD = {
    "batchcomplete": True,
    "query": {
        "pages": [
            {
                "pageid": 17834167,
                "index": 1,
                "title": "Rani ki Vav",
                "extract": "Rani ki Vav is a stepwell in Patan, Gujarat.",
                "fullurl": "https://en.wikipedia.org/wiki/Rani_ki_Vav",
                "canonicalurl": "https://en.wikipedia.org/wiki/Rani_ki_Vav",
            }
        ]
    },
}

PAGE_PAYLOAD = {
    "query": {
        "pages": [
            {
                "pageid": 17834167,
                "title": "Rani ki Vav",
                "canonicalurl": "https://en.wikipedia.org/wiki/Rani_ki_Vav",
                "extract": "Rani ki Vav is a stepwell in Patan. It was built in the 11th century.",
            }
        ]
    }
}

MEDIA_PAYLOAD = {
    "query": {
        "pages": [
            {
                "pageid": 17834167,
                "title": "Rani ki Vav",
                "canonicalurl": "https://en.wikipedia.org/wiki/Rani_ki_Vav",
                "thumbnail": {
                    "source": "https://upload.wikimedia.org/thumb/rani.jpg",
                    "width": 320,
                    "height": 240,
                },
                "pageimage": "Rani_ki_Vav_2019.jpg",
            }
        ]
    }
}

WIKIDATA_PAYLOAD = {
    "entities": {
        "Q187635": {
            "claims": {
                "P31": [
                    {
                        "mainsnak": {
                            "datavalue": {
                                "value": {"id": "Q5"}  # human → canonical "person"
                            }
                        }
                    }
                ]
            }
        }
    }
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sequence_client(statuses: list[int], payload: Any, calls: list[int]) -> httpx.AsyncClient:
    """Answer ``statuses`` in order (the last one repeats), counting calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        index = len(calls)
        calls.append(index)
        status = statuses[min(index, len(statuses) - 1)]
        if status >= 400:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(200, json=payload)

    return _client(handler)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def test_transient_statuses_are_retryable_and_permanent_ones_are_not():
    for status in (408, 425, 429, 500, 502, 503, 504):
        assert is_retryable_status(status), status
    for status in (400, 401, 403, 404, 410, 422):
        assert not is_retryable_status(status), status


def test_retry_after_is_parsed_only_in_seconds_form():
    assert parse_retry_after("2") == 2.0
    assert parse_retry_after(" 0.5 ") == 0.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert parse_retry_after("-3") is None


def test_backoff_grows_is_capped_and_jittered():
    policy = RetryPolicy(attempts=3, base_delay_seconds=0.25, max_delay_seconds=1.0)
    rng = random.Random(7)
    first = backoff_delay(0, policy, rng=rng)
    later = backoff_delay(2, policy, rng=rng)
    # Grows, but the capped attempt never exceeds the cap plus jitter.
    assert first >= 0.25
    assert later <= 1.0 * (1 + retry_module.JITTER_RATIO)
    assert later >= 1.0


def test_retry_after_wins_over_backoff_but_is_capped():
    policy = RetryPolicy(base_delay_seconds=0.25, max_delay_seconds=1.0)
    assert backoff_delay(0, policy, retry_after=0.9) == 0.9
    # A source asking for a minute must not make the request hang.
    assert backoff_delay(0, policy, retry_after=60) == retry_module.MAX_RETRY_AFTER_SECONDS


def test_attempts_can_be_configured_and_are_clamped():
    assert policy_from_environ({}).attempts == DEFAULT_POLICY.attempts
    assert policy_from_environ({"HG_SOURCE_RETRIES": "3"}).attempts == 3
    assert policy_from_environ({"HG_SOURCE_RETRIES": "99"}).attempts == retry_module.MAX_ATTEMPTS
    assert policy_from_environ({"HG_SOURCE_RETRIES": "0"}).attempts == 1
    # A typo must not break a search: the documented default applies.
    assert policy_from_environ({"HG_SOURCE_RETRIES": "two"}).attempts == DEFAULT_POLICY.attempts


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def test_a_transient_failure_is_retried_and_the_retry_wins():
    calls: list[int] = []
    waits: list[float] = []

    async def operation() -> str:
        calls.append(len(calls))
        if len(calls) == 1:
            raise TransientSourceError("HTTP 429", status=429)
        return "ok"

    result = await run_with_retries(
        operation,
        policy=RetryPolicy(attempts=2),
        sleep=lambda delay: _no_wait(waits, delay),
    )

    assert result == "ok"
    assert len(calls) == 2
    assert waits and waits[0] > 0


async def _no_wait(waits: list[float], delay: float) -> None:
    waits.append(delay)


async def test_attempts_are_bounded_and_the_last_error_is_reported():
    calls: list[int] = []

    async def operation() -> None:
        calls.append(len(calls))
        raise TransientSourceError("HTTP 503", status=503)

    with pytest.raises(TransientSourceError):
        await run_with_retries(
            operation,
            policy=RetryPolicy(attempts=3),
            sleep=lambda delay: _no_wait([], delay),
        )
    assert len(calls) == 3


async def test_a_permanent_error_is_never_retried():
    calls: list[int] = []

    async def operation() -> None:
        calls.append(len(calls))
        raise ValueError("our request was wrong")

    with pytest.raises(ValueError):
        await run_with_retries(operation, policy=RetryPolicy(attempts=3))
    assert len(calls) == 1


async def test_cancellation_is_never_retried():
    calls: list[int] = []

    async def operation() -> None:
        calls.append(len(calls))
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_with_retries(operation, policy=RetryPolicy(attempts=3))
    assert len(calls) == 1


async def test_the_time_budget_stops_retries_of_a_hanging_source():
    calls: list[int] = []
    waits: list[float] = []
    times = iter([0.0, 0.0])

    async def operation() -> None:
        calls.append(len(calls))
        raise TransientSourceError("timeout")

    with pytest.raises(TransientSourceError):
        await run_with_retries(
            operation,
            policy=RetryPolicy(attempts=5, base_delay_seconds=1.0, max_total_seconds=0.5),
            sleep=lambda delay: _no_wait(waits, delay),
            clock=lambda: next(times),
        )
    # One attempt, no sleep: waiting would exceed the budget.
    assert len(calls) == 1
    assert waits == []


async def test_on_retry_reports_each_retry():
    seen: list[tuple[int, float]] = []
    calls: list[int] = []

    async def operation() -> str:
        calls.append(len(calls))
        if len(calls) < 3:
            raise TransientSourceError("HTTP 500", status=500)
        return "ok"

    await run_with_retries(
        operation,
        policy=RetryPolicy(attempts=3),
        on_retry=lambda attempt, error, delay: seen.append((attempt, delay)),
        sleep=lambda delay: _no_wait([], delay),
    )
    assert [attempt for attempt, _ in seen] == [0, 1]


# ---------------------------------------------------------------------------
# Adapters: Wikipedia search
# ---------------------------------------------------------------------------


async def test_search_recovers_from_a_rate_limited_first_attempt():
    calls: list[int] = []
    client = _sequence_client([429, 200], SEARCH_PAYLOAD, calls)

    hits = await search_wikipedia("Rani ki Vav", client=client, environ={})

    assert [hit.title for hit in hits] == ["Rani ki Vav"]
    # The transient attempt was retried instead of being reported as empty.
    assert len(calls) == 2


async def test_search_counts_a_retry_for_the_api_notice():
    calls: list[int] = []
    stats: dict[str, int] = {}
    client = _sequence_client([503, 200], SEARCH_PAYLOAD, calls)

    hits = await search_wikipedia("Rani ki Vav", client=client, stats=stats, environ={})

    assert hits and stats["retries"] == 1
    assert "unavailable" not in stats


async def test_search_reports_unavailable_only_after_the_retries_fail():
    calls: list[int] = []
    stats: dict[str, int] = {}
    client = _sequence_client([503], SEARCH_PAYLOAD, calls)

    assert await search_wikipedia("Rani ki Vav", client=client, stats=stats, environ={}) == []
    assert stats["unavailable"] == 1
    assert len(calls) == DEFAULT_POLICY.attempts


async def test_search_does_not_retry_a_permanent_rejection():
    calls: list[int] = []
    stats: dict[str, int] = {}
    client = _sequence_client([403], SEARCH_PAYLOAD, calls)

    assert await search_wikipedia("Rani ki Vav", client=client, stats=stats, environ={}) == []
    assert len(calls) == 1  # 403 is our problem; retrying burns quota


async def test_a_successful_search_is_cached_and_not_refetched():
    calls: list[int] = []
    client = _sequence_client([200], SEARCH_PAYLOAD, calls)

    await search_wikipedia("Rani ki Vav", client=client, environ={})
    await search_wikipedia("Rani ki Vav", client=client, environ={})

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Adapters: page text / media / Wikidata never cache a failure
# ---------------------------------------------------------------------------


async def test_a_failed_page_fetch_is_not_cached_as_missing_text():
    calls: list[int] = []
    # The outage outlasts the attempt budget, so the *first* call fails…
    client = _sequence_client([503, 503, 200], PAGE_PAYLOAD, calls)

    assert await fetch_page_text("Rani ki Vav", client=client, environ={}) is None
    assert len(calls) == DEFAULT_POLICY.attempts

    page = await fetch_page_text("Rani ki Vav", client=client, environ={})
    assert page is not None
    assert "stepwell" in page.text
    assert len(calls) >= 2


async def test_a_failed_media_fetch_is_not_cached_as_no_image():
    calls: list[int] = []
    client = _sequence_client([503, 503, 200], MEDIA_PAYLOAD, calls)

    assert await fetch_page_media(["Rani ki Vav"], client=client, environ={}) == []

    media = await fetch_page_media(["Rani ki Vav"], client=client, environ={})
    assert media and media[0].thumbnail_url == "https://upload.wikimedia.org/thumb/rani.jpg"


async def test_a_failed_wikidata_lookup_does_not_cache_an_untyped_subject():
    calls: list[int] = []
    client = _sequence_client([503, 503, 200], WIKIDATA_PAYLOAD, calls)

    # An unanswered lookup yields the untyped placeholder — never a guessed type.
    first = await fetch_subject_classes(["Q187635"], client=client, environ={})
    assert first["Q187635"].type is None

    # …and is not cached, so the next lookup still learns the real type.
    classes = await fetch_subject_classes(["Q187635"], client=client, environ={})
    assert classes["Q187635"].type is not None
