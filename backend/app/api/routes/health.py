"""B1 — liveness and readiness endpoints.

``GET /api/health``  — liveness only: the process is up and serving.
                       Never checks dependencies, so a failing dependency
                       cannot make the service look dead.

``GET /api/ready``   — readiness: aggregates the configuration check plus
                       every registered dependency check and returns 503
                       when any of them fails. Checks may be sync or
                       async (the database check awaits a real
                       ``SELECT 1`` ping). Modules register checks with
                       :func:`register_dependency_check` instead of
                       changing this file.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single readiness check."""

    ok: bool
    detail: str | None = None


DependencyCheck = Callable[[], "CheckResult | Awaitable[CheckResult]"]

#: Registered dependency checks, keyed by check name.
_dependency_checks: dict[str, DependencyCheck] = {}


def register_dependency_check(name: str, check: DependencyCheck) -> None:
    """Register (or replace) a readiness check.

    App-level defaults live on ``app.state.dependency_checks`` (set by
    ``create_app`` and bound to *that app's* settings); this module-level
    registry exists for tests and subsystems and **overrides** app
    defaults with the same name.
    """
    _dependency_checks[name] = check


def clear_dependency_checks() -> None:
    """Remove all registered checks (used by tests)."""
    _dependency_checks.clear()


def configuration_check(settings) -> CheckResult:
    """Required-configuration check: reports missing variables by name."""
    if settings.missing_required:
        return CheckResult(
            ok=False,
            detail=(
                "Missing required environment variables: "
                + ", ".join(settings.missing_required)
            ),
        )
    return CheckResult(ok=True, detail="All required environment variables are set.")


def database_check(settings) -> DependencyCheck:
    """Factory: a readiness check that pings this app's database.

    Uses a short-lived engine built from *this app's* settings (never a
    guessed URL), disposes it immediately, and reports only a safe
    detail — connection strings and driver errors stay in the log.
    """

    async def check() -> CheckResult:
        url = (settings.database_url or "").strip()
        if not url:
            return CheckResult(ok=False, detail="DATABASE_URL is not configured.")

        from sqlalchemy import text

        from ...db.session import create_engine

        engine = create_engine(url)
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 — boundary check
            # Only the exception type is surfaced: driver messages can
            # contain connection strings or credentials.
            return CheckResult(
                ok=False, detail=f"Database ping failed ({type(exc).__name__})."
            )
        finally:
            await engine.dispose()
        return CheckResult(ok=True, detail="Database reachable (SELECT 1).")

    return check


async def _run_checks(request: Request, settings) -> dict[str, CheckResult]:
    # App defaults first, module-level registrations override them.
    app_checks: Mapping[str, DependencyCheck] = (
        getattr(request.app.state, "dependency_checks", None) or {}
    )
    merged: dict[str, DependencyCheck] = {**app_checks, **_dependency_checks}

    checks: dict[str, CheckResult] = {"configuration": configuration_check(settings)}
    for name, check in sorted(merged.items()):
        try:
            result = check()
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, CheckResult):
                raise TypeError(f"check returned {type(result).__name__}")
            checks[name] = result
        except Exception as exc:  # a crashing check is a failed check
            # Only the exception type is surfaced: messages and tracebacks
            # can carry connection strings or credentials.
            checks[name] = CheckResult(
                ok=False, detail=f"Check raised {type(exc).__name__}"
            )
    return checks


@router.get("/health", summary="Liveness probe")
def health(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
    }


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={503: {"description": "A required dependency is not ready"}},
)
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    checks = await _run_checks(request, settings)
    all_ok = all(result.ok for result in checks.values())

    payload = {
        "status": "ready" if all_ok else "not_ready",
        "checks": {
            name: {"ok": result.ok, "detail": result.detail}
            for name, result in checks.items()
        },
    }
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content=payload,
    )
