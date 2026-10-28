"""D4 — graph construction (shared-schema ``GraphResponse``).

Assembles the contract-shaped knowledge graph from extracted entities
and validated relationship candidates — the "construct the graph
response" step of B3's required processing order, *after* Module C4 has
evaluated evidence (its verdicts arrive through ``statuses``).

Guarantees (D4 acceptance criteria):

* **no fabricated edges** — every edge comes from a
  :class:`~app.services.relationship_service.RelationshipCandidate`
  (i.e. an explicit model proposal that survived validation); records
  and entities alone never produce one;
* **every edge reference points to a valid node** — endpoints are
  checked against the emitted node ids and anything unresolvable is
  dropped (defensive: the candidate builder already resolves them);
* **stable identifiers** — nodes keep ``ent-1..N`` and edges get
  ``rel-1..N`` in input order, so identical input yields an identical
  graph and the evidence endpoint can address a given edge;
* **statuses are passed through, never invented** — ``statuses`` is
  Module C4's evaluation output keyed by candidate id; only values in
  the contract's ``VERIFICATION_STATUSES`` are honoured, anything else
  falls back to the candidate's ``unverified``. A graph assembled with
  no evaluation at all therefore reports ``unverified``, never
  ``verified``.

``B4's GET /api/analysis/{id}/graph`` route keeps serving *persisted*
data directly; this service is the construction side used during the
analysis itself.
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from ..schemas.entity import AnalysisEntity
from ..schemas.graph import VERIFICATION_STATUSES, GraphEdge, GraphNode, GraphResponse
from .relationship_service import RelationshipCandidate

__all__ = ["build_graph"]


def build_graph(
    analysis_id: str,
    entities: Sequence[AnalysisEntity],
    candidates: Sequence[RelationshipCandidate],
    *,
    statuses: Optional[Mapping[str, str]] = None,
) -> GraphResponse:
    """Build the graph of one analysis from entities + candidates.

    ``statuses`` (optional): candidate id -> evaluated verification
    status, normally built from Module C4's ``EvidenceEvaluation``
    results by B3. Invalid or unknown values are ignored in favour of
    the candidate's own ``unverified`` status.
    """
    nodes: list[GraphNode] = []
    node_ids: set[str] = set()
    for entity in entities:
        if not entity.id or entity.id in node_ids:
            continue  # empty/duplicate ids would break edge addressing
        node_ids.add(entity.id)
        nodes.append(GraphNode(id=entity.id, label=entity.name, type=entity.type))

    edges: list[GraphEdge] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for candidate in candidates:
        if not candidate.id or candidate.id in seen_ids:
            continue  # edge ids must be unique (the evidence endpoint keys on them)
        if (candidate.source, candidate.target) in seen_pairs:
            continue  # mirrors uq_relationships_pair: one edge per ordered pair
        if candidate.source not in node_ids or candidate.target not in node_ids:
            continue  # never emit a dangling reference

        status = candidate.verification_status
        if statuses is not None:
            evaluated = statuses.get(candidate.id)
            if evaluated in VERIFICATION_STATUSES:
                status = evaluated

        seen_ids.add(candidate.id)
        seen_pairs.add((candidate.source, candidate.target))
        edges.append(
            GraphEdge(
                id=candidate.id,
                source=candidate.source,
                target=candidate.target,
                label=candidate.label,
                # Passed through exactly as evaluated — never coerced.
                verification_status=status,
            )
        )

    return GraphResponse(analysis_id=analysis_id, nodes=nodes, edges=edges)