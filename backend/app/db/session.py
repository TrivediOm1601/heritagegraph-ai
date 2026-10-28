"""C2 — async database session management.

Owned by Module C. Provides:

* async engine/session factories (URL scheme normalised to an async
  driver: ``postgresql://`` → ``postgresql+psycopg://``,
  ``sqlite://`` → ``sqlite+aiosqlite://``, so the B1 ``DATABASE_URL``
  convention keeps working unchanged),
* :func:`session_scope` — commit-on-success / rollback-on-failure
  transactional scope for orchestrators (B3),
* :func:`get_session` — a FastAPI dependency with the same semantics,
* the repository exception hierarchy and :func:`safe` — the decorator
  every repository function wears so **raw SQL/driver errors can never
  reach an API consumer** (acceptance criterion of C2); originals stay
  chained for server-side logs only.

Configuration rule (shared with B1/C1): no fabricated URLs — without
``DATABASE_URL`` the default engine fails with ``ConfigurationError``.
"""

from __future__ import annotations

import functools
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    SQLAlchemyError,
    TimeoutError as SATimeoutError,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.config import ConfigurationError, get_settings

__all__ = [
    "AsyncSession",
    "RepositoryError",
    "ConflictError",
    "NotFoundError",
    "DatabaseUnavailableError",
    "normalize_database_url",
    "create_engine",
    "create_session_factory",
    "get_engine",
    "get_session_factory",
    "reset_default_engine",
    "session_scope",
    "get_session",
    "safe",
]

# ---------------------------------------------------------------------------
# Exceptions (sanitised by construction: they carry friendly messages only)
# ---------------------------------------------------------------------------


class RepositoryError(Exception):
    """Base class for database errors that are safe to surface upward.

    Messages are written for humans and contain no SQL, no driver text
    and no connection details.
    """


class ConflictError(RepositoryError):
    """The operation conflicts with existing data or violates a constraint."""


class NotFoundError(RepositoryError):
    """The referenced row does not exist."""


class DatabaseUnavailableError(RepositoryError):
    """The database could not be reached / the connection was lost."""


def translate_db_error(exc: SQLAlchemyError) -> RepositoryError | None:
    """Map a driver/SQLAlchemy exception onto a sanitised repository error.

    Returns ``None`` for exceptions that are not database errors.
    """
    if isinstance(exc, IntegrityError):
        return ConflictError(
            "The operation conflicts with existing data or violates a "
            "data-integrity constraint."
        )
    if isinstance(
        exc, (OperationalError, InterfaceError, SATimeoutError, TimeoutError)
    ):
        return DatabaseUnavailableError(
            "The database is unavailable. Please try again later."
        )
    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return DatabaseUnavailableError(
            "The database connection was lost. Please try again later."
        )
    if isinstance(exc, SQLAlchemyError):
        return RepositoryError("A database error occurred.")
    return None


_F = TypeVar("_F", bound=Callable[..., Awaitable[Any]])


def safe(fn: _F) -> _F:
    """Decorator: translate unexpected database errors into sanitised ones.

    :class:`RepositoryError` subclasses raised deliberately inside the
    function pass through untouched; everything else SQLAlchemy-shaped is
    wrapped. The original stays chained (``raise ... from exc``) so the
    server log keeps the detail while API consumers never see it.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except RepositoryError:
            raise
        except SQLAlchemyError as exc:
            translated = translate_db_error(exc) or RepositoryError(
                "A database error occurred."
            )
            raise translated from exc

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Engines and session factories
# ---------------------------------------------------------------------------

#: Sync-style scheme -> async driver used by this project.
_ASYNC_DRIVERS = {
    "postgresql": "postgresql+psycopg",
    "postgres": "postgresql+psycopg",
    "sqlite": "sqlite+aiosqlite",
}


def normalize_database_url(url: str) -> str:
    """Return ``url`` with an explicit async driver scheme.

    Already-qualified schemes (``postgresql+psycopg://``, …) and unknown
    schemes are returned unchanged.
    """
    scheme, separator, rest = url.partition("://")
    if "+" in scheme or not separator:
        return url
    target = _ASYNC_DRIVERS.get(scheme)
    return f"{target}://{rest}" if target else url


def create_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Create an async engine for an explicit URL (no caching, no defaults)."""
    return create_async_engine(normalize_database_url(url), **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``engine``; objects survive commits so
    orchestrators can return them after ``session_scope`` commits."""
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Process-wide engine built from ``DATABASE_URL`` (fails when unset)."""
    url = (get_settings().database_url or "").strip()
    if not url:
        raise ConfigurationError(
            "DATABASE_URL is not set — the database layer will not guess a "
            "connection. Export DATABASE_URL before using repositories."
        )
    return create_engine(url)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Process-wide session factory (see :func:`get_engine`)."""
    return create_session_factory(get_engine())


def reset_default_engine() -> None:
    """Drop the cached engine/factory (tests, and after settings change)."""
    get_session_factory.cache_clear()
    get_engine.cache_clear()


# ---------------------------------------------------------------------------
# Transactional scopes
# ---------------------------------------------------------------------------


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Transactional scope for orchestrators.

    Commits on normal exit, rolls back on any error — a failed workflow
    (B3) can never leave half-written rows behind.
    """
    maker = factory if factory is not None else get_session_factory()
    async with maker() as session:
        try:
            yield session
        except SQLAlchemyError as exc:
            # A repository (or the caller) let a driver error escape: keep it
            # sanitised at this boundary too, never raw.
            await session.rollback()
            raise (
                translate_db_error(exc)
                or RepositoryError("A database error occurred.")
            ) from exc
        except BaseException:
            await session.rollback()
            raise
        else:
            try:
                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                raise (
                    translate_db_error(exc)
                    or RepositoryError("A database error occurred.")
                ) from exc


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency variant of :func:`session_scope`.

    Usage (Member 2's routes): ``session: AsyncSession =
    Depends(get_session)`` — commits after the handler returns, rolls
    back when it raises.
    """
    async with session_scope() as session:
        yield session
