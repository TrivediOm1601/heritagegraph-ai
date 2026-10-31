"""Session authentication, request-origin checks and rate limiting.

Three concerns live here because they all guard the same endpoints:

**Session resolution** (:func:`current_user_optional` /
:func:`require_user`) reads the HttpOnly cookie, hashes the token, and
looks the digest up in ``user_sessions``. The identity used for ownership
checks is therefore always derived from a validated server-side session —
never from a request body, query parameter or header the client controls.

**CSRF** — the cookie is ``SameSite=Lax`` and ``HttpOnly``, and unsafe
methods must additionally pass :func:`verify_same_origin`: when the
browser sends ``Origin`` (or ``Referer``), its host must match the host
this request was sent to. A cross-site form/fetch therefore cannot ride an
existing session. Requests without either header (curl, server-to-server
tests) are still subject to cookie possession, which a cross-site attacker
cannot obtain.

**Rate limiting** — a small in-process fixed-window limiter for
credential and expensive endpoints. It is intentionally simple: it
protects a single-process deployment from trivial brute force and
accidental hammering. A multi-instance deployment should put a shared
limiter (API gateway / Redis) in front; that is documented as a limitation
rather than pretended to be solved here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from fastapi import Request

from ..db.session import RepositoryError, session_scope
from ..repositories.user_repository import get_session, touch_session
from ..services.auth_service import SESSION_COOKIE, session_token_hash
from .config import ConfigurationError, Settings, get_settings
from .errors import (
    DependencyUnavailableError,
    ForbiddenError,
    RateLimitedError,
    UnauthorizedError,
)

__all__ = [
    "CurrentUser",
    "AUTH_UNAVAILABLE",
    "current_user_optional",
    "require_user",
    "verify_same_origin",
    "set_session_cookie",
    "clear_session_cookie",
    "session_cookie_secure",
    "RateLimiter",
    "limit_credentials",
    "limit_analysis",
    "limit_export",
    "limit_archive",
]

#: Safe message when the session store itself cannot be reached.
AUTH_UNAVAILABLE = (
    "Sign-in is unavailable: the account database could not be reached. "
    "Please try again."
)


@dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller (no password material, ever)."""

    id: str
    email: str
    display_name: str | None

    def to_payload(self) -> dict[str, str | None]:
        return {"id": self.id, "email": self.email, "displayName": self.display_name}


def _settings(request: Request | None) -> Settings:
    settings = getattr(getattr(request, "app", None), "state", None)
    resolved = getattr(settings, "settings", None)
    return resolved if isinstance(resolved, Settings) else get_settings()


async def current_user_optional(request: Request) -> CurrentUser | None:
    """The session's user, or ``None`` when there is no valid session.

    A database/config failure is surfaced as the canonical 503 — an
    infrastructure outage must never look like "signed out", which would
    silently drop a user into an anonymous flow.
    """
    token = (request.cookies.get(SESSION_COOKIE) or "").strip()
    if not token:
        return None

    digest = session_token_hash(token)
    try:
        async with session_scope() as session:
            found = await get_session(session, digest)
            if found is None:
                return None
            row, user = found
            await touch_session(session, row.token_hash)
            return CurrentUser(
                id=user.id, email=user.email, display_name=user.display_name
            )
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(AUTH_UNAVAILABLE) from exc


async def require_user(request: Request) -> CurrentUser:
    """Dependency: the authenticated user, or a canonical 401.

    Without a configured database the session store cannot exist at all, so
    the honest answer is the canonical ``503 dependency_unavailable`` — not
    a 401 that would tell the user to sign in to a service that cannot
    check credentials.
    """
    settings = _settings(request)
    if not (settings.database_url or "").strip():
        raise DependencyUnavailableError(AUTH_UNAVAILABLE)
    user = await current_user_optional(request)
    if user is None:
        raise UnauthorizedError("Sign in to continue.")
    return user


def _origin_host(value: str) -> str | None:
    """Host portion of an Origin/Referer header (``None`` when unusable)."""
    candidate = (value or "").strip()
    if not candidate or candidate == "null":
        return None
    # Origin is "scheme://host[:port]"; Referer is a full URL.
    without_scheme = candidate.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0]
    return host.lower() or None


def _configured_origins(environ: Mapping[str, str] | None = None) -> set[str]:
    """Authorities this deployment declares as its own.

    ``APP_ORIGIN`` is the public address of the app; ``CORS_ORIGINS`` is the
    explicit allow-list used when the frontend is served elsewhere. Both are
    compared by authority (host:port), never by scheme, so terminating TLS
    at a proxy needs no extra configuration.
    """
    import os

    env = os.environ if environ is None else environ
    values: list[str] = []
    for name in ("APP_ORIGIN", "CORS_ORIGINS"):
        raw = (env.get(name) or "").strip()
        if not raw:
            continue
        values.extend(piece for piece in raw.split(",") if piece.strip())
    hosts = {_origin_host(value) for value in values}
    return {host for host in hosts if host}


def verify_same_origin(request: Request) -> None:
    """Reject a state-changing request that came from another origin.

    Only the *authority* is compared, so a reverse proxy terminating TLS does
    not need to be reconfigured for scheme mismatches, and a proxy that
    rewrites ``Host`` (Vite's ``changeOrigin``, common nginx setups) is still
    accepted when the deployment declares its public origin in
    ``APP_ORIGIN``/``CORS_ORIGINS``. When neither header is present the
    request is allowed to proceed and relies on the cookie's
    ``SameSite=Lax`` protection — a cross-site page cannot attach the
    cookie, and browsers always send ``Origin`` for cross-origin writes.
    """
    host = (request.headers.get("host") or "").lower()
    allowed = {host} if host else set()
    allowed |= _configured_origins()
    if not allowed:
        return  # no Host header (unusual): cookie rules still apply

    origin = request.headers.get("origin")
    if origin:
        if _origin_host(origin) not in allowed:
            raise ForbiddenError(
                "This request was blocked because it came from a different origin."
            )
        return

    referer = request.headers.get("referer")
    if referer:
        if _origin_host(referer) not in allowed:
            raise ForbiddenError(
                "This request was blocked because it came from a different origin."
            )


def session_cookie_secure(request: Request) -> bool:
    """``Secure`` cookies in production or on an HTTPS request."""
    settings = _settings(request)
    if settings.environment == "production":
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return request.url.scheme == "https" or forwarded.lower() == "https"


def set_session_cookie(
    request: Request, response, token: str, *, max_age: int
) -> None:
    """Attach the session cookie with hardened attributes."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(request),
        path="/",
    )


def clear_session_cookie(request: Request, response) -> None:
    """Expire the session cookie (logout)."""
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(request),
    )


class RateLimiter:
    """Fixed-window counter keyed by an arbitrary string.

    Thread-safety: the FastAPI event loop is single-threaded, and this
    limiter is only touched from ``async`` request handlers, so a plain
    dict is sufficient. It is per-process by design (see module docstring).
    """

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, tuple[float, int]] = {}

    def hit(self, key: str) -> None:
        """Record one attempt, raising :class:`RateLimitedError` when over."""
        now = time.monotonic()
        window_start, count = self._hits.get(key, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        count += 1
        self._hits[key] = (window_start, count)
        if count > self.limit:
            retry_after = max(1, int(self.window_seconds - (now - window_start)))
            raise RateLimitedError(
                "Too many attempts. Please wait a moment and try again.",
                retry_after=retry_after,
            )

    def prune(self, *, keep: int = 4096) -> None:
        """Drop old windows so the map cannot grow without bound."""
        if len(self._hits) <= keep:
            return
        now = time.monotonic()
        self._hits = {
            key: state
            for key, state in self._hits.items()
            if now - state[0] < self.window_seconds
        }


def client_ip(request: Request) -> str:
    """Best-effort client address for rate-limit keys.

    ``X-Forwarded-For`` is trusted only when the deployment says it is
    behind a proxy (``TRUST_PROXY_HEADERS=1``), otherwise a client could
    rotate its own rate-limit key with a header.
    """
    settings = _settings(request)
    import os

    if (os.environ.get("TRUST_PROXY_HEADERS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


#: Credential endpoints: 10 attempts per 5 minutes per address.
limit_credentials = RateLimiter(limit=10, window_seconds=300)
#: Uploads: 30 per hour per user (each upload spends provider credits).
limit_analysis = RateLimiter(limit=30, window_seconds=3600)
#: Exports: 20 per hour per user (PDF generation is CPU-bound).
limit_export = RateLimiter(limit=20, window_seconds=3600)
#: Archive uploads: 60 per hour per user (each file may spend OCR credits).
limit_archive = RateLimiter(limit=60, window_seconds=3600)
