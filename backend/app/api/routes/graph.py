"""B2/B4 — knowledge-graph route.

Thin handler: validates the analysis id, then reads the persisted graph
from Module C's storage (entities linked to the run + its relationships).

* analysis unknown → ``404 not_found``
* analysis known but with no entities/relationships → ``200`` with
  empty ``nodes``/``edges`` (the UI shows its empty state)
* database/config unavailable → canonical ``503 dependency_unavailable``

The *construction* of graph data during an analysis (extraction,
relationship candidates) belongs to Module D via B3; this endpoint only
serves what has actually been persisted.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ...core.config import ConfigurationError
from ...core.errors import (
    DependencyUnavailableError,
    NotFoundError,
    error_response,
)
from ...core.security import CurrentUser, require_user
from ...db.session import RepositoryError, session_scope
from ...repositories.analysis_repository import (
    get_analysis_run,
    list_analysis_entities,
)
from ...repositories.relationship_repository import list_relationships
from ...schemas.graph import GraphEdge, GraphNode, GraphResponse

router = APIRouter(tags=["graph"])

#: Canonical failure text when the database itself cannot serve reads.
GRAPH_UNAVAILABLE = (
    "The knowledge graph is unavailable: the database could not be "
    "reached."
)


@router.get(
    "/analysis/{analysis_id}/graph",
    response_model=GraphResponse,
    summary="Get the knowledge graph of an analysis",
    description=(
        "Returns the nodes (extracted entities) and edges (relationships) "
        "of one analysis. `nodes`/`edges` are empty when there is nothing "
        "to show, which the UI renders as an empty state."
    ),
    responses={
        401: error_response("Sign in to read a graph."),
        404: error_response("No analysis exists with that id."),
        422: error_response("The analysis id is missing or malformed."),
        503: error_response("The database serving the graph is unavailable."),
    },
)
async def get_graph(
    user: Annotated[CurrentUser, Depends(require_user)],
    analysis_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            pattern=r"^[^\s]+$",
            description="Analysis id returned by POST /api/analysis.",
        ),
    ],
) -> GraphResponse:
    try:
        async with session_scope() as session:
            run = await get_analysis_run(session, analysis_id)
            # Owner-scoped: another user's analysis is a 404, never a leak.
            if run is None or run.user_id != user.id:
                raise NotFoundError(f"No analysis exists with id '{analysis_id}'.")
            links = await list_analysis_entities(session, analysis_id)
            relationships = await list_relationships(session, analysis_id)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(GRAPH_UNAVAILABLE) from exc

    nodes = [
        GraphNode(id=entity.id, label=entity.name, type=entity.entity_type)
        for entity, _link in links
    ]
    edges = [
        GraphEdge(
            id=relationship.id,
            source=relationship.source_entity_id,
            target=relationship.target_entity_id,
            label=relationship.label,
            # Passed through exactly as stored — never coerced.
            verification_status=relationship.verification_status,
        )
        for relationship in relationships
    ]
    return GraphResponse(analysis_id=analysis_id, nodes=nodes, edges=edges)
