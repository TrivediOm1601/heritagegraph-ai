"""Subject media — the photograph shown with a historical subject.

Feature support for the search page: a subject's overview should carry a
real photograph of *that* subject, with its provenance, and a related-topic
card should be able to show a thumbnail. Both come from the same source the
records already come from (MediaWiki), so no new provider is introduced.

Two API calls, both documented and both verified live against the endpoint
before this module was written:

1. ``action=query&titles=…&prop=pageimages|pageterms|pageprops|info``
   — the rendered ``thumbnail``/``original`` image URL, the page's own
   one-line description (``pageterms``) and its Wikidata id;
2. ``action=query&titles=File:…&prop=imageinfo&iiprop=url|mime|extmetadata``
   on Commons — the file's description page, its license and its credit,
   so a displayed photograph always says where it came from.

Rules this module keeps:

* **Nothing is invented.** A field the API did not return stays ``None``,
  and the UI shows a placeholder rather than a guessed caption, credit or
  license. Image URLs are only accepted when they are real ``http(s)``
  URLs from the payload.
* **A source outage never breaks a search.** Any timeout, HTTP error,
  malformed body or unexpected shape degrades to ``[]`` with a log line —
  the search then renders without an image.
* **Two transports, deliberately.** Production uses the stdlib client in a
  worker thread (Wikimedia's robot-policy layer answers 403 to httpx from
  this environment — measured, see :mod:`app.integrations.wikipedia`); an
  injected ``httpx.AsyncClient`` is the fixture seam for tests.
* **Attribution is first-class.** ``credit``/``license_name`` are returned
  exactly as Commons spells them, never summarised away.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

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
from .wikipedia import DEFAULT_LANG, user_agent

logger = get_logger("sources.wikimedia")

__all__ = [
    "COMMONS_API_PATH",
    "MEDIA_THUMB_WIDTH",
    "PageMedia",
    "clear_cache",
    "fetch_page_media",
    "page_media_index",
]

#: Article metadata comes from the language Wikipedia…
API_PATH = "https://{lang}.wikipedia.org/w/api.php"
#: …and the files themselves (description page, license, credit) from Commons.
COMMONS_API_PATH = "https://commons.wikimedia.org/w/api.php"

#: Rendered width for subject photographs / related-topic thumbnails.
MEDIA_THUMB_WIDTH = 800
#: Upper bound on titles per request (MediaWiki allows 50; a search never
#: needs more than its ranked hits).
MAX_MEDIA_TITLES = 12

DEFAULT_TIMEOUT_SECONDS = 8.0
PROVIDER = "historical source service"
ENV_TIMEOUT = "HG_SOURCE_TIMEOUT"

_CACHE_TTL_SECONDS = 600.0
_CACHE_MAX_ENTRIES = 64

_TAG_RE = re.compile(r"<[^>]+>")

_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list["PageMedia"]]] = {}


@dataclass(frozen=True)
class PageMedia:
    """Citable media metadata of one page — never a guessed image."""

    title: str
    page_id: Optional[str] = None
    article_url: Optional[str] = None
    #: The page's own short description (``pageterms``), e.g.
    #: "Prime Minister of India". Used as the topic/subject subtitle.
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    original_url: Optional[str] = None
    file_name: Optional[str] = None
    #: File description page on Commons — the attribution link.
    file_page_url: Optional[str] = None
    license_name: Optional[str] = None
    license_url: Optional[str] = None
    #: Author/attribution as Commons records it (HTML stripped).
    credit: Optional[str] = None
    image_description: Optional[str] = None
    #: Wikidata item id (``Q…``) — a stable identity for the subject.
    wikibase_item: Optional[str] = None

    @property
    def has_image(self) -> bool:
        return bool(self.thumbnail_url or self.original_url)


def clear_cache() -> None:
    """Drop the media cache (tests)."""
    _cache.clear()


def _timeout(environ: Optional[Mapping[str, str]]) -> float:
    try:
        return read_timeout(PROVIDER, ENV_TIMEOUT, default=DEFAULT_TIMEOUT_SECONDS, environ=environ)
    except ProviderConfigurationError as exc:
        logger.warning("source timeout misconfigured, using default: %s", exc)
        return DEFAULT_TIMEOUT_SECONDS


def _plain_text(value: Any) -> Optional[str]:
    """Strip the HTML MediaWiki puts inside ``extmetadata`` values."""
    if not isinstance(value, str):
        return None
    text = html.unescape(_TAG_RE.sub(" ", value))
    text = " ".join(text.split())
    return text or None


def _url_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else None


def _fetch_via_urllib(url: str, *, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent(), "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


async def _fetch_once(
    url: str, *, client: Optional[httpx.AsyncClient], timeout: float, label: str
) -> Any:
    """One attempt: parsed JSON, or ``None`` for a permanent failure.

    Retryable outcomes raise :class:`TransientSourceError` (the wrapper
    retries them); a 4xx or a malformed body returns ``None`` immediately.
    """
    if client is None:
        try:
            status, body = await asyncio.to_thread(_fetch_via_urllib, url, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if is_retryable_status(exc.code):
                raise TransientSourceError(
                    f"HTTP {exc.code}",
                    retry_after=parse_retry_after(
                        getattr(exc, "headers", None)
                        and exc.headers.get("Retry-After")
                    ),
                    status=exc.code,
                ) from exc
            logger.warning("%s answered HTTP %d", label, exc.code)
            return None
        except Exception as exc:  # noqa: BLE001 — media is an enhancement only
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

    request = httpx.Request(
        "GET", url, headers={"User-Agent": user_agent(), "Accept": "application/json"}
    )
    request.extensions["timeout"] = {
        "connect": timeout,
        "read": timeout,
        "write": timeout,
        "pool": timeout,
    }
    try:
        response = await client.send(request)
    except httpx.TimeoutException as exc:
        raise TransientSourceError(f"timed out after {timeout:g}s") from exc
    except httpx.HTTPError as exc:
        raise TransientSourceError(type(exc).__name__) from exc
    if response.status_code >= 400:
        if is_retryable_status(response.status_code):
            raise TransientSourceError(
                f"HTTP {response.status_code}",
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
                status=response.status_code,
            )
        logger.warning("%s answered HTTP %d", label, response.status_code)
        return None
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        logger.warning("%s returned non-JSON content", label)
        return None


async def _fetch_json(
    url: str,
    *,
    client: Optional[httpx.AsyncClient],
    timeout: float,
    label: str,
    environ: Optional[Mapping[str, str]] = None,
) -> Any:
    """GET ``url`` and parse JSON with bounded retries of transient faults.

    Returns ``None`` when the source could not be read. Because the caller
    distinguishes ``None`` from an empty payload, a failure is never cached
    as "this subject has no image".
    """
    return await fetch_json_with_retries(
        lambda: _fetch_once(url, client=client, timeout=timeout, label=label),
        policy=policy_from_environ(environ),
        label=label,
    )


def _media_request_url(titles: Sequence[str], *, lang: str) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": "|".join(titles),
            "redirects": "1",
            "prop": "pageimages|pageterms|pageprops|info",
            "piprop": "thumbnail|original|name",
            "pithumbsize": str(MEDIA_THUMB_WIDTH),
            "inprop": "url",
            "format": "json",
            "formatversion": "2",
        }
    )
    return f"{API_PATH.format(lang=lang)}?{params}"


def _fileinfo_request_url(files: Sequence[str]) -> str:
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "titles": "|".join(files),
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": str(MEDIA_THUMB_WIDTH),
            "format": "json",
            "formatversion": "2",
        }
    )
    return f"{COMMONS_API_PATH}?{params}"


def _parse_media(payload: Any) -> list[PageMedia]:
    """Parse a ``prop=pageimages|pageterms|pageprops|info`` payload."""
    if not isinstance(payload, dict):
        return []
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list):
        return []

    media: list[PageMedia] = []
    for page in pages:
        if not isinstance(page, dict) or page.get("missing"):
            continue
        title = str(page.get("title") or "").strip()
        if not title:
            continue

        thumbnail = page.get("thumbnail")
        original = page.get("original")
        terms = page.get("terms")
        descriptions = terms.get("description") if isinstance(terms, dict) else None
        description: Optional[str] = None
        if isinstance(descriptions, list):
            for item in descriptions:
                description = _plain_text(item)
                if description:
                    break
        props = page.get("pageprops")
        wikibase_item = None
        if isinstance(props, dict):
            wikibase_item = str(props.get("wikibase_item") or "").strip() or None

        media.append(
            PageMedia(
                title=title,
                page_id=str(page.get("pageid")) if page.get("pageid") is not None else None,
                article_url=_url_or_none(page.get("canonicalurl") or page.get("fullurl")),
                description=description,
                thumbnail_url=_url_or_none(
                    thumbnail.get("source") if isinstance(thumbnail, dict) else None
                ),
                original_url=_url_or_none(
                    original.get("source") if isinstance(original, dict) else None
                ),
                file_name=str(page.get("pageimage") or "").strip() or None,
                wikibase_item=wikibase_item,
            )
        )
    return media


def _parse_fileinfo(payload: Any) -> dict[str, dict[str, Optional[str]]]:
    """Parse an ``imageinfo`` payload into ``{file title: metadata}``."""
    found: dict[str, dict[str, Optional[str]]] = {}
    if not isinstance(payload, dict):
        return found
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list):
        return found

    for page in pages:
        if not isinstance(page, dict):
            continue
        title = str(page.get("title") or "").strip()
        infos = page.get("imageinfo")
        if not title or not isinstance(infos, list) or not infos:
            continue
        info = infos[0]
        if not isinstance(info, dict):
            continue
        meta = info.get("extmetadata")
        values: dict[str, Any] = meta if isinstance(meta, dict) else {}

        def field(*names: str) -> Optional[str]:
            for name in names:
                entry = values.get(name)
                if isinstance(entry, dict):
                    parsed = _plain_text(entry.get("value"))
                    if parsed:
                        return parsed
            return None

        license_url_entry = values.get("LicenseUrl")
        found[title] = {
            "file_page_url": _url_or_none(
                info.get("descriptionurl") or info.get("descriptionshorturl")
            ),
            "license_name": field("LicenseShortName", "UsageTerms", "License"),
            "license_url": _url_or_none(
                license_url_entry.get("value") if isinstance(license_url_entry, dict) else None
            ),
            "credit": field("Artist", "Credit", "Attribution"),
            "image_description": field("ImageDescription", "ObjectName"),
        }
    return found


def _file_lookup_key(name: str) -> str:
    """Normalise a file name so ``File:A_b.jpg`` matches ``File:A b.jpg``."""
    cleaned = name.replace("_", " ").strip()
    if cleaned.lower().startswith("file:"):
        cleaned = cleaned[len("file:") :]
    return cleaned.casefold()


def _with_file_metadata(
    item: PageMedia, info: Mapping[str, Mapping[str, Optional[str]]]
) -> PageMedia:
    if not item.file_name:
        return item
    meta = info.get(f"File:{item.file_name}")
    if meta is None:
        target = _file_lookup_key(item.file_name)
        meta = next(
            (
                value
                for title, value in info.items()
                if _file_lookup_key(title) == target
            ),
            None,
        )
    if meta is None:
        return item
    return PageMedia(
        title=item.title,
        page_id=item.page_id,
        article_url=item.article_url,
        description=item.description,
        thumbnail_url=item.thumbnail_url,
        original_url=item.original_url,
        file_name=item.file_name,
        file_page_url=meta.get("file_page_url"),
        license_name=meta.get("license_name"),
        license_url=meta.get("license_url"),
        credit=meta.get("credit"),
        image_description=meta.get("image_description"),
        wikibase_item=item.wikibase_item,
    )


def page_media_index(media: Sequence[PageMedia]) -> dict[str, PageMedia]:
    """Index media by casefolded title, for matching retrieval hits."""
    return {item.title.strip().casefold(): item for item in media}


async def fetch_page_media(
    titles: Sequence[str],
    *,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    lang: str = DEFAULT_LANG,
) -> list[PageMedia]:
    """Fetch page images + descriptions + license metadata for ``titles``.

    Returns ``[]`` on any failure (a subject then renders a placeholder).
    Every field the source does not supply stays ``None``; no URL, credit or
    license is ever synthesised.
    """
    cleaned: list[str] = []
    for title in titles:
        text = " ".join((title or "").split())
        if text and text not in cleaned:
            cleaned.append(text)
    cleaned = cleaned[:MAX_MEDIA_TITLES]
    if not cleaned:
        return []

    key = (lang, tuple(cleaned))
    entry = _cache.get(key)
    if entry is not None:
        expires, cached = entry
        if time.monotonic() <= expires:
            return cached

    timeout = _timeout(environ)
    payload = await _fetch_json(
        _media_request_url(cleaned, lang=lang),
        client=client,
        timeout=timeout,
        label="historical source media",
        environ=environ,
    )
    if payload is None:
        # The source could not be read. Returning [] here is the graceful
        # degradation, but it must NOT be cached: remembering an outage as
        # "this subject has no photograph" is what made a recovered source
        # keep showing a placeholder for the whole TTL.
        return []
    media = _parse_media(payload)

    files = [f"File:{item.file_name}" for item in media if item.file_name]
    if files:
        file_payload = await _fetch_json(
            _fileinfo_request_url(files),
            client=client,
            timeout=timeout,
            label="historical source file metadata",
            environ=environ,
        )
        info = _parse_fileinfo(file_payload)
        if info:
            media = [_with_file_metadata(item, info) for item in media]

    if len(_cache) >= _CACHE_MAX_ENTRIES:
        for stale in list(_cache)[: _CACHE_MAX_ENTRIES // 4]:
            _cache.pop(stale, None)
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, media)
    return media
