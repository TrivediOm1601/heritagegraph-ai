"""B1 — FastAPI application factory.

Run it with::

    cd backend && uvicorn app.main:app

``create_app`` accepts explicit :class:`Settings` so tests can exercise
different environments; without arguments it loads configuration from the
environment (lazily, cached). Startup never requires credentials to be
present — missing required configuration is reported by ``/api/ready``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api.router import create_api_router
from .api.routes.health import database_check
from .core.config import Settings, get_settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging, get_logger


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""
    resolved = settings if settings is not None else get_settings()
    configure_logging(resolved)
    logger = get_logger("startup")

    if resolved.missing_required:
        logger.warning(
            "missing required configuration: %s (service will start, "
            "readiness stays failing until it is provided)",
            ", ".join(resolved.missing_required),
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info("startup: %s", resolved.summary())
        yield
        logger.info("shutdown complete")

    app = FastAPI(
        title=resolved.app_name,
        description="HeritageGraph AI — historical image analysis API.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    # Readiness defaults for THIS app (bound to these settings; the
    # module-level check registry can override them per name).
    app.state.dependency_checks = {"database": database_check(resolved)}

    # Cookie sessions are cross-origin-capable only when the operator lists
    # the exact frontend origins. There is deliberately no "*" default: a
    # wildcard cannot be combined with credentials, and silently allowing
    # every origin for a cookie-authenticated API is a cross-site request
    # forgery hole. Same-origin deployments (the documented one) need none.
    cors_origins = [
        origin.strip().rstrip("/")
        for origin in (os.environ.get("CORS_ORIGINS") or "").split(",")
        if origin.strip() and origin.strip() != "*"
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Accept", "Content-Type", "X-Requested-With"],
            max_age=600,
        )
        logger.info("CORS enabled for %d configured origin(s)", len(cors_origins))

    # One error envelope for every non-2xx response (B2 error contract).
    install_error_handlers(app)

    # Every request gets an id: echoed as the X-Request-Id response header,
    # embedded in error bodies (agreed format) and used to correlate log
    # lines. Server-generated only — a client-supplied header is never
    # trusted for log correlation.
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    # Route modules are registered on a single router under the configured
    # prefix (default /api).
    app.include_router(create_api_router(resolved), prefix=resolved.api_prefix)
    logger.info("routes registered under prefix %s", resolved.api_prefix)

    return app


#: Default application instance for ``uvicorn app.main:app``.
app = create_app()
