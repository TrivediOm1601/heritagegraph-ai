"""Live historical source — MediaWiki search API (Wikipedia).

Retrieval add-on for Module C's ``search_historical_records``: when
``HG_LIVE_SOURCES=1`` the query is *also* run against the public
MediaWiki search API, so a query about a real heritage site can retrieve
a real, citable record instead of being limited to the 20-row
development seed. Opt-in by design (mirrors the demo-mode rule): with
the variable unset or falsy this module performs no network I/O at all,
so tests and offline deployments stay hermetic.

Source contract (checked live against the API before implementation —
see REPAIR_PLAN.md):

* endpoint ``GET https://en.wikipedia.org/w/api.php`` with
  ``action=query&generator=search&gsrnamespace=0&prop=extracts|info``
  — each hit carries ``title``, a plain-text intro ``extract`` and the
  canonical page URL, so every returned record is genuinely citable;
* a descriptive ``User-Agent`` is sent (MediaWiki policy requires one);
* **transport note (verified, not assumed):** Wikimedia's robot-policy
  layer answers **HTTP 403 to httpx** for every request from this
  environment while the *same* URL, parameters and User-Agent succeed
  through ``curl`` and Python's stdlib ``urllib`` (bisected header- and
  TLS-wise on 2026-10-27). The production path therefore uses the
  stdlib client (``asyncio.to_thread``); the httpx ``client`` seam is
  kept for ``MockTransport`` fixtures, which never dial out;
* response handling is defensive: unexpected JSON degrades to *no live
  results* (logged), never to an exception that would break local
  search, and never to fabricated records.

Provenance: returned records are plain dicts with
``source="Wikipedia"`` and the page's canonical URL; ids are namespaced
``wiki:<pageid>`` so they can never collide with curated seed ids.

This is *retrieval*, not verification: a Wikipedia hit is a candidate
source the user can open, not a confirmed identification.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import httpx

from ..core.logging import get_logger
from . import ProviderConfigurationError, read_timeout
from .retry import (
    TransientSourceError,
    fetch_json_with_retries,
    is_retryable_status,
    parse_retry_after,
    policy_from_environ,
)

logger = get_logger("sources.wikipedia")

__all__ = [
    "ENV_LIVE_SOURCES",
    "ENV_TIMEOUT",
    "PROVIDER",
    "USER_AGENT_PRODUCT",
    "user_agent",
    "PageContent",
    "SourceHit",
    "live_sources_enabled",
    "fetch_page_text",
    "search_wikipedia",
    "clear_cache",
]

#: Opt-in switch for live source retrieval (default: off).
ENV_LIVE_SOURCES = "HG_LIVE_SOURCES"
#: Optional request timeout in seconds (default below).
ENV_TIMEOUT = "HG_SOURCE_TIMEOUT"

PROVIDER = "historical source service"
DEFAULT_TIMEOUT_SECONDS = 8.0

#: Override the User-Agent (name + contact) sent to the source API.
ENV_USER_AGENT = "HG_SOURCE_USER_AGENT"
#: Deployment origin, used as the default contact when the override is unset.
ENV_APP_ORIGIN = "APP_ORIGIN"
#: Fallback contact when neither variable is set (local development).
_DEV_ORIGIN = "http://localhost:8000"

#: MediaWiki's robot policy requires a descriptive agent **with a contact
#: URL or email**, and throttles agents without one: verified live — the
#: bare name answered HTTP 429, while the same request carrying this form
#: answered 200 immediately. The default therefore includes the deployment
#: origin as its contact (the same rule the image proxy follows).
USER_AGENT_PRODUCT = "HeritageGraphAI/1.0"


def user_agent(environ: Optional[Mapping[str, str]] = None) -> str:
    """The User-Agent sent to the historical source API.

    ``HG_SOURCE_USER_AGENT`` wins when set (a deployment should put its own
    URL/email in it); otherwise the deployment origin is used as the contact.
    """
    env = os.environ if environ is None else environ
    configured = (env.get(ENV_USER_AGENT) or "").strip()
    if configured:
        return configured[:256]
    contact = (env.get(ENV_APP_ORIGIN) or "").strip() or _DEV_ORIGIN
    return f"{USER_AGENT_PRODUCT} (+{contact}; heritage image research)"

API_PATH = "https://{lang}.wikipedia.org/w/api.php"
DEFAULT_LANG = "en"
#: Intro extracts are short; sentence cap keeps payloads small.
MAX_SENTENCES = 3
MAX_QUERY_LENGTH = 200
#: Full-page extracts are used to build a grounded briefing; cap them so a
#: pathological page cannot bloat memory or the cache.
MAX_PAGE_CHARS = 400_000

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

#: Simple TTL cache so one analysis (which probes several entity names)
#: does not re-fetch identical queries. Keyed by (lang, query, limit).
_CACHE_TTL_SECONDS = 600.0
_CACHE_MAX_ENTRIES = 256
_cache: dict[tuple[str, str, int], tuple[float, list["SourceHit"]]] = {}


@dataclass(frozen=True)
class SourceHit:
    """One live search hit — the citable fields only."""

    page_id: str
    title: str
    url: str
    extract: str | None

    @property
    def record_id(self) -> str:
        return f"wiki:{self.page_id}"


@dataclass(frozen=True)
class PageContent:
    """One retrieved page's plain text, ready to be quoted with a citation.

    ``text`` is MediaWiki ``explaintext`` output: headings appear as
    ``== Section ==`` lines and paragraphs are separated by blank lines,
    which is what :mod:`app.services.briefing_service` parses. Nothing
    here is generated — it is the source's own wording.
    """

    page_id: str
    title: str
    url: str
    text: str


def live_sources_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when ``HG_LIVE_SOURCES`` explicitly opts in."""
    import os

    env = os.environ if environ is None else environ
    return (env.get(ENV_LIVE_SOURCES) or "").strip().lower() in _TRUE_VALUES


def clear_cache() -> None:
    """Drop the TTL caches (tests)."""
    _cache.clear()
    _page_cache.clear()


def _timeout(environ: Optional[Mapping[str, str]]) -> float:
    try:
        return read_timeout(
            PROVIDER, ENV_TIMEOUT, default=DEFAULT_TIMEOUT_SECONDS, environ=environ
        )
    except ProviderConfigurationError as exc:
        # Retrieval must never crash a search over a bad timeout value:
        # log the misconfiguration and use the documented default.
        logger.warning("source timeout misconfigured, using default: %s", exc)
        return DEFAULT_TIMEOUT_SECONDS


def _cached(key: tuple[str, str, int]) -> list[SourceHit] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires, hits = entry
    if time.monotonic() > expires:
        _cache.pop(key, None)
        return None
    return hits


def _remember(key: tuple[str, str, int], hits: list[SourceHit]) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        # Cheap bounded eviction: drop the oldest-inserted entries.
        for stale in list(_cache)[: _CACHE_MAX_ENTRIES // 4]:
            _cache.pop(stale, None)
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, hits)


def _request_url(query: str, *, limit: int, lang: str) -> str:
    """Fully-qualified MediaWiki API URL with the query parameters."""
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "0",
            "gsrlimit": str(max(1, limit)),
            "prop": "extracts|info",
            "inprop": "url",
            "exintro": "1",
            "explaintext": "1",
            "exsentences": str(MAX_SENTENCES),
            "format": "json",
            "formatversion": "2",
        }
    )
    return f"{API_PATH.format(lang=lang)}?{params}"


def _fetch_via_urllib(
    url: str, *, timeout: float, environ: Optional[Mapping[str, str]] = None
) -> tuple[int, bytes]:
    """Production transport: stdlib HTTP GET → ``(status, body)``.

    Raises only on transport failure (timeout, DNS, TLS) — the caller
    treats that as "no live results". See the module docstring for why
    this is not httpx.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent(environ),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def _client_request(
    url: str, *, timeout: float, environ: Optional[Mapping[str, str]] = None
) -> httpx.Request:
    """httpx request for the injected (test/fixture) client seam."""
    request = httpx.Request(
        "GET",
        url,
        headers={"User-Agent": user_agent(environ), "Accept": "application/json"},
    )
    request.extensions["timeout"] = {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }
    return request


def _build_request(
    query: str,
    *,
    limit: int,
    lang: str,
    timeout: float,
    environ: Optional[Mapping[str, str]] = None,
) -> httpx.Request:
    """httpx request used by the injected (test/fixture) client seam."""
    return _client_request(
        _request_url(query, limit=limit, lang=lang), timeout=timeout, environ=environ
    )


def _retry_after_of(headers: Any) -> float | None:
    """``Retry-After`` from an httpx header map or a urllib header object."""
    try:
        value = headers.get("Retry-After") if headers is not None else None
    except Exception:  # noqa: BLE001 — a header quirk must not break a fetch
        return None
    return parse_retry_after(value)


async def _fetch_payload_once(
    url: str,
    *,
    client: Optional[httpx.AsyncClient],
    timeout: float,
    environ: Optional[Mapping[str, str]] = None,
    label: str = "historical source",
) -> Any:
    """One HTTP attempt, parsed as JSON.

    Retryable outcomes (429, transient 5xx, timeout, connection failure)
    raise :class:`TransientSourceError` so the retrying wrapper can try
    again; permanent ones (4xx, malformed JSON) log and return ``None``.
    Never returns fabricated data.
    """
    if client is None:
        try:
            status, body = await asyncio.to_thread(
                _fetch_via_urllib, url, timeout=timeout, environ=environ
            )
        except urllib.error.HTTPError as exc:
            if is_retryable_status(exc.code):
                raise TransientSourceError(
                    f"HTTP {exc.code}",
                    retry_after=_retry_after_of(getattr(exc, "headers", None)),
                    status=exc.code,
                ) from exc
            logger.warning("%s answered HTTP %d", label, exc.code)
            return None
        except Exception as exc:  # noqa: BLE001 — timeouts, DNS, TLS, resets
            raise TransientSourceError(type(exc).__name__) from exc
        if status >= 400:
            if is_retryable_status(status):
                raise TransientSourceError(f"HTTP {status}", status=status)
            logger.warning("%s answered HTTP %d", label, status)
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("%s returned non-JSON content", label)
            return None

    try:
        response = await client.send(
            _client_request(url, timeout=timeout, environ=environ)
        )
    except httpx.TimeoutException as exc:
        raise TransientSourceError(f"timed out after {timeout:g}s") from exc
    except httpx.HTTPError as exc:
        raise TransientSourceError(type(exc).__name__) from exc
    if response.status_code >= 400:
        if is_retryable_status(response.status_code):
            raise TransientSourceError(
                f"HTTP {response.status_code}",
                retry_after=_retry_after_of(response.headers),
                status=response.status_code,
            )
        logger.warning("%s answered HTTP %d", label, response.status_code)
        return None
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        logger.warning("%s returned non-JSON content", label)
        return None


def _parse(payload: Any) -> list[SourceHit]:
    """Turn the MediaWiki response into hits (defensive: never raises)."""
    if not isinstance(payload, dict):
        return []
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list):
        return []

    hits: list[tuple[int, SourceHit]] = []
    for position, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "").strip()
        page_id = page.get("pageid")
        url = str(page.get("canonicalurl") or page.get("fullurl") or "").strip()
        if not title or page_id is None or not url.startswith(("http://", "https://")):
            # Missing/odd entries are skipped — never guessed at.
            continue
        extract = page.get("extract")
        hits.append(
            (
                int(page.get("index") or position + 1),
                SourceHit(
                    page_id=str(page_id),
                    title=title,
                    url=url,
                    extract=str(extract).strip() if isinstance(extract, str) and extract.strip() else None,
                ),
            )
        )
    hits.sort(key=lambda item: item[0])
    return [hit for _, hit in hits]


def _note(stats: Optional[dict[str, int]], key: str) -> None:
    """Record one attempt outcome in the caller's counters (best effort)."""
    if stats is None:
        return
    stats[key] = stats.get(key, 0) + 1


async def search_wikipedia(
    query: str,
    *,
    limit: int = 5,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    lang: str = DEFAULT_LANG,
    stats: Optional[dict[str, int]] = None,
) -> list[SourceHit]:
    """Search Wikipedia for ``query``; ``[]`` on any problem (never a raise).

    Two transports, deliberately:

    * **production** (``client=None``): the stdlib urllib fetch in a worker
      thread — Wikimedia's robot-policy layer 403s httpx here (verified,
      see module docstring);
    * **fixture seam** (``client`` injected): the httpx client, so tests
      run against ``MockTransport`` with zero egress.

    Every failure — timeout, connection, HTTP status, malformed JSON —
    logs and returns ``[]``: a live-source outage must degrade local
    search, not break it and not fabricate results.

    ``stats`` is an optional caller-owned counter map. It lets the API
    distinguish *"the source had nothing"* from *"the source could not be
    asked"* (e.g. Wikimedia's rate limiter) without any process-global
    state: ``stats["ok"]`` / ``stats["cached"]`` count answered requests,
    ``stats["unavailable"]`` counts failed ones. The result list itself is
    unchanged either way.
    """
    cleaned = " ".join((query or "").split())[:MAX_QUERY_LENGTH]
    if not cleaned:
        return []

    key = (lang, cleaned.casefold(), limit)
    cached = _cached(key)
    if cached is not None:
        _note(stats, "cached")
        return cached

    timeout = _timeout(environ)
    url = _request_url(cleaned, limit=limit, lang=lang)

    def _count_retry(_attempt: int, _error: TransientSourceError, _delay: float) -> None:
        # Lets the API report "served after a retry" instead of silently
        # hiding that the source was briefly unavailable.
        _note(stats, "retries")

    # The one attempt that used to be final: a 429 or a transient 5xx here
    # was reported as "no live results", which is why the *first* search
    # could come back thin while repeating it immediately worked.
    payload = await fetch_json_with_retries(
        lambda: _fetch_payload_once(
            url,
            client=client,
            timeout=timeout,
            environ=environ,
            label="historical source",
        ),
        policy=policy_from_environ(environ),
        label="historical source",
        on_retry=_count_retry,
    )
    if payload is None:
        _note(stats, "unavailable")
        return []

    hits = _parse(payload)
    _remember(key, hits)
    _note(stats, "ok")
    return hits


# ---------------------------------------------------------------------------
# Full page text (grounded briefings)
# ---------------------------------------------------------------------------

#: Separate cache: page bodies are much larger than intro extracts.
_page_cache: dict[tuple[str, str], tuple[float, Optional["PageContent"]]] = {}
_page_cache_max = 64

#: Media cache: keyed by (lang, tuple(titles)) so one search does not refetch.
_media_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list["PageMedia"]]] = {}
_media_cache_max = 64


def _page_url(title: str, *, lang: str) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": title,
            "redirects": "1",
            "prop": "extracts|info",
            "inprop": "url",
            "explaintext": "1",
            "format": "json",
            "formatversion": "2",
        }
    )
    return f"{API_PATH.format(lang=lang)}?{params}"


def _parse_page(payload: Any, *, requested: str) -> Optional[PageContent]:
    """Defensive parse of a ``query.pages`` payload (never raises)."""
    if not isinstance(payload, dict):
        return None
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or not pages:
        return None
    page = pages[0]
    if not isinstance(page, dict) or page.get("missing"):
        return None
    title = str(page.get("title") or "").strip()
    url = str(page.get("canonicalurl") or page.get("fullurl") or "").strip()
    text = page.get("extract")
    if not title or not isinstance(text, str) or not text.strip():
        return None
    if not url.startswith(("http://", "https://")):
        return None
    page_id = page.get("pageid")
    return PageContent(
        page_id=str(page_id) if page_id is not None else requested,
        title=title,
        url=url,
        text=text[:MAX_PAGE_CHARS],
    )


def _cached_page(key: tuple[str, str]) -> tuple[bool, Optional[PageContent]]:
    """``(hit, page)`` — a cached *miss* (``None``) is still a hit."""
    entry = _page_cache.get(key)
    if entry is None:
        return False, None
    expires, page = entry
    if time.monotonic() > expires:
        _page_cache.pop(key, None)
        return False, None
    return True, page


def _remember_page(key: tuple[str, str], page: Optional[PageContent]) -> None:
    if len(_page_cache) >= _page_cache_max:
        for stale in list(_page_cache)[: _page_cache_max // 4]:
            _page_cache.pop(stale, None)
    _page_cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, page)


async def fetch_page_text(
    title: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    lang: str = DEFAULT_LANG,
) -> Optional[PageContent]:
    """Fetch one page's full plain text; ``None`` on any problem.

    Same two transports as :func:`search_wikipedia` (stdlib urllib in
    production, an injected httpx client for fixtures) and the same
    degradation rule: every failure logs and returns ``None`` so a source
    outage reduces the briefing to the retrieval hits instead of failing
    the request or, worse, inventing text.

    Transient failures are retried (bounded) and are **not cached**: the
    old code remembered a failed fetch as ``None`` for the full TTL, so a
    momentary 429 hid that article's text for ten minutes — including from
    the user's next attempt. Only a real answer is cached, and a page the
    source says does not exist (``missing``) is a real answer.
    """
    cleaned = " ".join((title or "").split())[:MAX_QUERY_LENGTH]
    if not cleaned:
        return None

    key = (lang, cleaned.casefold())
    hit, cached = _cached_page(key)
    if hit:
        return cached

    timeout = _timeout(environ)
    url = _page_url(cleaned, lang=lang)

    payload = await fetch_json_with_retries(
        lambda: _fetch_payload_once(
            url,
            client=client,
            timeout=timeout,
            environ=environ,
            label="historical source page",
        ),
        policy=policy_from_environ(environ),
        label="historical source page",
    )
    if payload is None:
        # Deliberately not cached: a transient outage must not hide the
        # article from the user's very next search.
        return None

    page = _parse_page(payload, requested=cleaned)
    _remember_page(key, page)
    return page
