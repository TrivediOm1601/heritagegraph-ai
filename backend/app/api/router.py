"""B1 — router assembly.

Every route module of the API is registered on one router, which
``app.main`` mounts under the configured prefix (default ``/api``).

B1 registers liveness/readiness only. Later backend parts (analysis,
search, graph, evidence) add their routers here as they land, so route
registration stays in a single place.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..core.config import Settings
from .routes import (
    analysis,
    auth,
    diagnostics,
    evidence,
    graph,
    health,
    history,
    media,
    search,
)


def create_api_router(settings: Settings) -> APIRouter:
    """Build the API router.

    ``settings`` is passed in so route modules that need configuration
    (feature flags, limits) can receive it when they are added.
    """
    router = APIRouter()

    # B1: liveness and readiness (paths become /health and /ready under
    # the mounted prefix, i.e. /api/health and /api/ready).
    router.include_router(health.router, tags=["health"])

    # B6: accounts and sessions (signup/login/logout/me).
    router.include_router(auth.router)

    # B2: the canonical contract endpoints.
    router.include_router(analysis.router)
    router.include_router(history.router)
    router.include_router(search.router)
    router.include_router(graph.router)
    router.include_router(evidence.router)

    # B5: same-origin image proxy so subject photographs do not depend on the
    # browser being able to reach a third-party image host.
    router.include_router(media.router)

    # B5: safe self-check ("which setting is missing?") for the running app.
    router.include_router(diagnostics.router)

    return router
