"""B5 — same-origin media proxy for subject photographs and thumbnails.

Why this exists
---------------
The subject photograph and the related-topic thumbnails are hosted by
Wikimedia (``upload.wikimedia.org`` / ``thumb.wikimedia.org``). Browsers
normally load them directly, which makes image *display* depend on a
third-party host being reachable from the user's browser: privacy
extensions and shields lists, DNS filtering, corporate proxies, VPNs and
offline networks break exactly that one request while every same-origin
``/api`` call keeps working. The visible symptom is "search works, images
do not", and it looks browser-specific because two browsers on the same
machine can resolve and allow different hosts.

Fetching the bytes through this backend makes image display depend only on
the application's own origin. It is deliberately **not** an open proxy:

* only ``https`` URLs on a fixed allowlist of Wikimedia image hosts;
* every redirect hop is re-checked against the same allowlist (max 2);
* the upstream response must be an image content-type and is capped in
  size, so a hostile or broken upstream cannot stream unbounded data;
* no cookies, authorization headers or user-identifying data are sent
  upstream;
* attribution is unaffected — the UI keeps showing credit, license and the
  file/source page links, which still point at Wikimedia.

Set ``HG_MEDIA_PROXY=off`` to disable the route (it then answers 503 and
the frontend falls back to loading the original URL directly).
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

from ...core.errors import (
    BadRequestError,
    DependencyUnavailableError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from ...core.logging import get_logger

router = APIRouter(prefix="/media", tags=["media"])

logger = get_logger("media")

PROVIDER = "image host"
#: Opt-out switch: "off"/"0"/"false"/"no" disables the proxy.
ENV_DISABLED = "HG_MEDIA_PROXY"

#: Hosts whose images may be proxied. Adding a host here is the only way to
#: widen the proxy — a caller never gets to choose an arbitrary URL.
ALLOWED_IMAGE_HOSTS = frozenset(
    {
        "upload.wikimedia.org",
        "thumb.wikimedia.org",
    }
)

#: Raster formats the UI displays, plus SVG (served with a restrictive CSP and
#: nosniff, and rendered by the browser as an <img>, never as a document).
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/avif",
        "image/tiff",
        "image/svg+xml",
    }
)

#: Hard cap on a proxied image (Wikimedia renders are far below this).
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: Upstream timeout (connect + read).
UPSTREAM_TIMEOUT_SECONDS = 12.0

#: Redirect hops allowed before giving up (each re-validated).
MAX_REDIRECTS = 2

#: User-Agent override. Wikimedia's User-Agent policy requires a descriptive
#: agent *with a contact URL or email*, and its CDN answers 403 ("Please
#: respect our robot policy") to agents that do not carry one. Operators
#: should set this to their project URL or a contact address.
ENV_USER_AGENT = "HG_MEDIA_USER_AGENT"
#: Deployment origin, used as the default contact when the override is unset.
ENV_APP_ORIGIN = "APP_ORIGIN"

#: Fallback contact when neither variable is set (local development).
_DEV_ORIGIN = "http://localhost:8000"

#: Client factory (monkeypatched by tests to inject a mock transport).
_client_factory = httpx.AsyncClient

#: Small in-memory cache — the same Wikimedia guidance that asks for a
#: compliant agent asks for caching, and one subject's photograph is
#: requested by every viewer of that page.
CACHE_MAX_ENTRIES = 24
CACHE_MAX_BYTES = 12 * 1024 * 1024
CACHE_TTL_SECONDS = 600.0


@dataclass(frozen=True)
class _CachedImage:
    content_type: str
    body: bytes
    stored_at: float


_cache: "OrderedDict[str, _CachedImage]" = OrderedDict()
_cache_bytes = 0


def clear_cache() -> None:
    """Drop the proxy cache (tests)."""
    global _cache_bytes
    _cache.clear()
    _cache_bytes = 0


def _cache_get(url: str) -> Optional[_CachedImage]:
    entry = _cache.get(url)
    if entry is None:
        return None
    if time.monotonic() - entry.stored_at > CACHE_TTL_SECONDS:
        _cache.pop(url, None)
        return None
    _cache.move_to_end(url)
    return entry


def _cache_put(url: str, entry: _CachedImage) -> None:
    global _cache_bytes
    if len(entry.body) > CACHE_MAX_BYTES:
        return
    previous = _cache.pop(url, None)
    if previous is not None:
        _cache_bytes -= len(previous.body)
    _cache[url] = entry
    _cache_bytes += len(entry.body)
    while _cache and (len(_cache) > CACHE_MAX_ENTRIES or _cache_bytes > CACHE_MAX_BYTES):
        _, evicted = _cache.popitem(last=False)
        _cache_bytes -= len(evicted.body)


def user_agent(environ: Optional[Mapping[str, str]] = None) -> str:
    """The User-Agent sent upstream.

    Defaults to a descriptive agent whose contact is the deployment origin,
    because a request without one is refused by the image host (403).
    """
    env = os.environ if environ is None else environ
    configured = (env.get(ENV_USER_AGENT) or "").strip()
    if configured:
        return configured
    contact = (env.get(ENV_APP_ORIGIN) or "").strip() or _DEV_ORIGIN
    return f"HeritageGraphAI/0.1 (+{contact})"

_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "disable", "disabled"})


def proxy_disabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``HG_MEDIA_PROXY`` explicitly turns the proxy off."""
    env = os.environ if environ is None else environ
    return (env.get(ENV_DISABLED) or "").strip().lower() in _DISABLED_VALUES


def _checked_target(raw: str) -> str:
    """Validate an image URL; returns the normalised URL to fetch."""
    candidate = (raw or "").strip()
    if not candidate:
        raise BadRequestError("A source image URL is required.")

    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise BadRequestError("Only https image URLs can be proxied.")
    if parts.username or parts.password:
        raise BadRequestError("Image URLs must not carry credentials.")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_IMAGE_HOSTS:
        raise BadRequestError(
            "That image host is not served by this proxy."
        )
    if parts.port not in (None, 443):
        raise BadRequestError("Image URLs must use the default https port.")
    return candidate


async def _open_stream(client: httpx.AsyncClient, target: str) -> httpx.Response:
    """GET ``target``, following at most :data:`MAX_REDIRECTS` allowed hops."""
    current = target
    for _ in range(MAX_REDIRECTS + 1):
        request = client.build_request("GET", current)
        response = await client.send(request, stream=True)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            await response.aclose()
            if not location:
                raise DependencyUnavailableError(
                    "The image host sent a redirect without a target."
                )
            current = _checked_target(urljoin(current, location))
            continue
        return response
    raise DependencyUnavailableError("The image host redirected too many times.")


def _image_response(body: bytes, content_type: str) -> Response:
    """One cacheable, non-sniffable image response (cached or fetched)."""
    return Response(
        content=body,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get(
    "",
    summary="Same-origin image proxy",
    response_class=Response,
    responses={
        200: {"content": {"image/*": {}}, "description": "The image bytes."},
        400: {"description": "The URL is not a proxied Wikimedia image URL"},
        404: {"description": "The image no longer exists at the source"},
        503: {"description": "The image host could not be reached"},
    },
)
async def media(
    url: str = Query(
        ...,
        min_length=1,
        max_length=2048,
        description="Absolute https URL on an allowed Wikimedia image host.",
    ),
) -> Response:
    """Serve an upstream image from this origin, so image display never
    depends on the browser being able to reach a third-party host."""
    if proxy_disabled():
        raise DependencyUnavailableError(
            "The image proxy is disabled (HG_MEDIA_PROXY); load image URLs directly."
        )

    target = _checked_target(url)

    cached = _cache_get(target)
    if cached is not None:
        return _image_response(cached.body, cached.content_type)

    try:
        async with _client_factory(
            timeout=UPSTREAM_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": user_agent(), "Accept": "image/*"},
        ) as client:
            response = await _open_stream(client, target)
            try:
                if response.status_code == 404:
                    raise NotFoundError(
                        "The image is no longer available at the source."
                    )
                if response.status_code != 200:
                    raise DependencyUnavailableError(
                        "The image host did not return the image "
                        f"(HTTP {response.status_code})."
                    )

                content_type = (
                    (response.headers.get("content-type") or "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise UnsupportedMediaTypeError(
                        "The source did not return a supported image format."
                    )

                declared = (response.headers.get("content-length") or "").strip()
                if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                    raise PayloadTooLargeError("The source image is too large.")

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise PayloadTooLargeError("The source image is too large.")
                    chunks.append(chunk)
            finally:
                await response.aclose()
    except httpx.HTTPError as exc:
        logger.warning(
            "media proxy: upstream request failed for host %s: %s",
            urlsplit(target).hostname,
            type(exc).__name__,
        )
        raise DependencyUnavailableError(
            "The image could not be loaded from its source right now."
        ) from exc

    if not chunks:
        raise DependencyUnavailableError("The image host returned an empty response.")

    body = b"".join(chunks)
    _cache_put(target, _CachedImage(content_type, body, time.monotonic()))
    return _image_response(body, content_type)
