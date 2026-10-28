"""B2/B4/B7 — historical text-search routes.

Thin handlers: validate the query against the shared contract (same
minimum length as the browser guard), then hand off to Module C's
retrieval service. Two failure classes stay strictly distinct:

* **no records matched** → ``200`` with ``results: []``;
* **the record store is unavailable** (no ``DATABASE_URL``, unreachable
  database) → the canonical ``503 dependency_unavailable`` — a failed
  search is never an empty result list, so the UI can keep
  "nothing matched" distinct from "the search failed".

Endpoints:

* ``GET /search`` — records + grounded briefing.
* ``GET /search/graph`` — the same retrieval expressed as a knowledge
  graph (subject → retrieved records → supported co-mentions).

Authenticated searches are also written to that user's analysis history.
The write is best-effort: a history failure must never break a search the
user is waiting for, and it never changes the response.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request

from ...core.config import ConfigurationError
from ...core.errors import (
    DependencyUnavailableError,
    UnprocessableEntityError,
    error_response,
)
from ...core.logging import get_logger
from ...core.security import CurrentUser, current_user_optional
from ...db.session import RepositoryError, session_scope
from ...schemas.graph import SearchGraphResponse
from ...schemas.search import (
    MAX_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
    HistoricalMatch,
    SearchResponse,
)
from ...services.briefing_service import build_briefing
from ...services.history_service import record_text_search
from ...services.retrieval_service import (
    RetrievedRecord,
    normalize_query,
    search_historical_records,
)
from ...services.search_graph_service import build_search_graph

logger = get_logger("search")

router = APIRouter(tags=["search"])

#: Canonical failure text when the record store itself cannot serve the
#: query (kept in ``responses`` documentation below).
SEARCH_UNAVAILABLE = (
    "Search is unavailable: the historical record store could not be "
    "reached."
)
GRAPH_UNAVAILABLE = (
    "The search graph is unavailable: the historical record store could not "
    "be reached."
)

_QUERY_CONSTRAINTS: dict[str, object] = {
    "min_length": MIN_QUERY_LENGTH,
    "max_length": MAX_QUERY_LENGTH,
}


class _SearchOutcome:
    """Everything one retrieval produced (shared by the two endpoints)."""

    __slots__ = ("hits", "briefing", "notice", "stats")

    def __init__(self, hits, briefing, notice, stats) -> None:
        self.hits: list[RetrievedRecord] = hits
        self.briefing = briefing
        self.notice: Optional[str] = notice
        self.stats: dict[str, int] = stats


async def _run_search(query: str) -> _SearchOutcome:
    """Run retrieval + briefing, translating infrastructure faults to 503.

    The briefing is built outside the failure boundary on purpose: a
    source page that cannot be fetched degrades the explanation, never the
    search.
    """
    stats: dict[str, int] = {}
    try:
        async with session_scope() as session:
            hits = await search_historical_records(session, query, stats=stats)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(SEARCH_UNAVAILABLE) from exc

    briefing = None
    if hits:
        try:
            briefing = await build_briefing(query, hits)
        except Exception as exc:  # noqa: BLE001 — enrichment must not break search
            logger.warning(
                "briefing construction failed: %s", type(exc).__name__, exc_info=exc
            )

    notice = None
    if stats.get("unavailable"):
        notice = (
            "The live historical source could not be reached or rate-limited "
            "this request, so these results may be incomplete. Please try "
            "again in a moment."
        )
        logger.info(
            "search served with a degraded live source (unavailable=%d, ok=%d)",
            stats.get("unavailable", 0),
            stats.get("ok", 0),
        )

    return _SearchOutcome(hits, briefing, notice, stats)


async def _record_search(user: Optional[CurrentUser], outcome: _SearchOutcome, query: str) -> None:
    """Store an authenticated search in the user's history (best effort)."""
    if user is None:
        return
    try:
        matches = [HistoricalMatch(**hit.to_match()) for hit in outcome.hits]
        briefing_payload = (
            outcome.briefing.model_dump(mode="json", by_alias=True)
            if outcome.briefing is not None
            else None
        )
        async with session_scope() as session:
            await record_text_search(
                session,
                user_id=user.id,
                query=normalize_query(query),
                results=matches,
                briefing=briefing_payload,
                notice=outcome.notice,
            )
    except Exception as exc:  # noqa: BLE001 — history is not worth failing a search
        logger.warning(
            "could not record a search in history: %s", type(exc).__name__, exc_info=exc
        )


def _require_query(query: str) -> str:
    # Mirror the browser guard exactly: it trims before checking length.
    if len(query.strip()) < MIN_QUERY_LENGTH:
        raise UnprocessableEntityError(
            f"Enter at least {MIN_QUERY_LENGTH} characters to search."
        )
    return query


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search historical records",
    description=(
        "Runs a text search over historical records and returns the "
        "records that matched. An empty `results` list means nothing "
        "matched; a failed search is an error response instead. When the "
        "caller is signed in, the search is added to their history."
    ),
    responses={
        422: error_response(
            f"The `query` parameter is missing or shorter than {MIN_QUERY_LENGTH} characters."
        ),
        503: error_response("The historical record store is unavailable."),
    },
)
async def search(
    request: Request,
    query: Annotated[
        str,
        Query(
            description="Text to search for (at least 2 characters, after trimming).",
            **_QUERY_CONSTRAINTS,
        ),
    ],
) -> SearchResponse:
    _require_query(query)
    outcome = await _run_search(query)

    user = await current_user_optional(request)
    await _record_search(user, outcome, query)

    return SearchResponse(
        query=normalize_query(query),
        results=[HistoricalMatch(**hit.to_match()) for hit in outcome.hits],
        briefing=outcome.briefing,
        notice=outcome.notice,
    )


@router.get(
    "/search/graph",
    response_model=SearchGraphResponse,
    summary="Knowledge graph for a historical text search",
    description=(
        "Returns the same retrieval as GET /api/search expressed as a graph: "
        "the queried subject, one node per retrieved record, and edges only "
        "where the records' own text supports them. `limitations` states "
        "honestly when the relationship data is thin."
    ),
    responses={
        422: error_response(
            f"The `query` parameter is missing or shorter than {MIN_QUERY_LENGTH} characters."
        ),
        503: error_response("The historical record store is unavailable."),
    },
)
async def search_graph(
    query: Annotated[
        str,
        Query(
            description="Text to search for (at least 2 characters, after trimming).",
            **_QUERY_CONSTRAINTS,
        ),
    ],
) -> SearchGraphResponse:
    _require_query(query)
    try:
        outcome = await _run_search(query)
    except DependencyUnavailableError as exc:
        raise DependencyUnavailableError(GRAPH_UNAVAILABLE) from exc

    return build_search_graph(
        query, outcome.hits, briefing=outcome.briefing, notice=outcome.notice
    )
