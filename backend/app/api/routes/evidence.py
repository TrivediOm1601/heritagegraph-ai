"""B2/B4 — relationship evidence route.

Thin handler: validates both ids, then reads the persisted evidence for
the requested relationship from Module C's storage.

* analysis unknown, or relationship not part of that analysis → ``404``
* relationship known with no stored evidence → ``200`` with an empty
  ``evidence`` list (missing evidence is data, not an error)
* database/config unavailable → canonical ``503 dependency_unavailable``

The *evaluation* that produces evidence and verification status is
Module C's evidence service, invoked by B3 once Module D's candidates
exist; this endpoint only reports what was actually persisted.
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
from ...repositories.analysis_repository import get_analysis_run
from ...repositories.evidence_repository import list_evidence
from ...repositories.relationship_repository import get_relationship
from ...schemas.evidence import EvidenceItem, EvidenceResponse

router = APIRouter(tags=["evidence"])

#: Canonical failure text when the database itself cannot serve reads.
EVIDENCE_UNAVAILABLE = (
    "Evidence is unavailable: the database could not be reached."
)


@router.get(
    "/analysis/{analysis_id}/relationships/{relationship_id}/evidence",
    response_model=EvidenceResponse,
    summary="Get the evidence behind one relationship",
    description=(
        "Returns the supporting evidence and the backend's actual "
        "verification status for a single relationship edge of the graph."
    ),
    responses={
        401: error_response("Sign in to read evidence."),
        404: error_response("No analysis or relationship exists with those ids."),
        422: error_response("The analysis id or relationship id is missing or malformed."),
        503: error_response("The database serving evidence is unavailable."),
    },
)
async def get_evidence(
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
    relationship_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            pattern=r"^[^\s]+$",
            description="Relationship id taken from the graph's `edges[].id`.",
        ),
    ],    ) -> EvidenceResponse:
    try:
        async with session_scope() as session:
            run = await get_analysis_run(session, analysis_id)
            # Owner-scoped: the evidence of another user's analysis is a 404.
            if run is None or run.user_id != user.id:
                raise NotFoundError(f"No analysis exists with id '{analysis_id}'.")
            # Scoped to the analysis: an edge of another run is a 404, not
            # a cross-analysis data leak.
            relationship = await get_relationship(
                session, relationship_id, analysis_id=analysis_id
            )
            if relationship is None:
                raise NotFoundError(
                    f"No relationship '{relationship_id}' exists for "
                    f"analysis '{analysis_id}'."
                )
            items = await list_evidence(session, relationship_id)
    except (ConfigurationError, RepositoryError) as exc:
        raise DependencyUnavailableError(EVIDENCE_UNAVAILABLE) from exc

    return EvidenceResponse(
        relationship_id=relationship.id,
        # The backend's actual stored status — never a default of 'verified'.
        verification_status=relationship.verification_status,
        summary=relationship.summary,
        evidence=[
            EvidenceItem(
                id=item.id,
                source=item.source,
                excerpt=item.excerpt,
                url=item.url,
            )
            for item in items
        ],
        notes=relationship.notes,
    )
