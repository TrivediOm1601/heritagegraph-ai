"""B2 — knowledge-graph schemas (canonical contract).

Mirrors ``frontend/src/types/api.ts``: nodes carry readable labels and an
optional layout hint; edges carry source/target plus the backend's actual
verification status (passed through unchanged — unknown values are never
coerced to 'verified').
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

#: Statuses the UI renders distinctly. Kept as a free string (like the
#: frontend's ``VerificationStatus | string``) so a value produced by
#: Module C/D is passed through rather than rejected; the canonical values
#: are documented on the field.
VERIFICATION_STATUSES: tuple[str, ...] = (
    "verified",
    "disputed",
    "unsupported",
    "unverified",
)


class GraphNodePosition(BaseModel):
    """Optional layout hint; when present the client places the node there."""

    x: float
    y: float


class GraphNode(BaseModel):
    """A node of the knowledge graph (one extracted entity)."""

    id: str = Field(min_length=1, max_length=128, description="Node id (matches the entity id).")
    label: str = Field(min_length=1, max_length=512, description="Readable label shown on the node.")
    type: Optional[str] = Field(
        default=None, max_length=64, description="Entity category (person, place, event, date, ...)."
    )
    position: Optional[GraphNodePosition] = Field(
        default=None, description="Optional pre-computed layout position."
    )


class GraphEdge(BaseModel):
    """A relationship edge of the knowledge graph."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(min_length=1, max_length=128, description="Relationship id (used by the evidence endpoint).")
    source: str = Field(min_length=1, max_length=128, description="Id of the source node.")
    target: str = Field(min_length=1, max_length=128, description="Id of the target node.")
    label: Optional[str] = Field(
        default=None, max_length=512, description="Relationship description shown on the edge."
    )
    verification_status: Optional[str] = Field(
        default=None,
        alias="verificationStatus",
        description=(
            "Backend verification status: one of "
            + ", ".join(VERIFICATION_STATUSES)
            + ". Passed through exactly as the backend reports it; an unknown "
            "or absent value is rendered as 'unverified', never 'verified'."
        ),
    )


class GraphEvidence(BaseModel):
    """One source backing a text-search graph edge."""

    source: str = Field(min_length=1, max_length=256, description="Where the evidence comes from.")
    excerpt: Optional[str] = Field(default=None, max_length=4000)
    url: Optional[str] = Field(default=None, max_length=1024)


class SearchGraphEdge(GraphEdge):
    """A text-search edge with the evidence that produced it."""

    evidence: list[GraphEvidence] = Field(
        default_factory=list,
        description="Records that support this edge (never fabricated).",
    )


class SearchGraphResponse(BaseModel):
    """Payload of GET /api/search/graph.

    Built from the records a text search actually retrieved: the queried
    subject, one node per retrieved record, and edges only where the
    records' own text supports them. No entity or relationship is invented
    to make the graph look populated.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    query: str = Field(min_length=1, max_length=512)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[SearchGraphEdge] = Field(default_factory=list)
    sources: list[GraphEvidence] = Field(
        default_factory=list, description="The retrieved sources behind the graph."
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Honest notes about thin or partial relationship data.",
    )
    notice: Optional[str] = Field(default=None, max_length=512)


class GraphResponse(BaseModel):
    """Payload of GET /api/analysis/{analysis_id}/graph."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    analysis_id: str = Field(
        alias="analysisId", min_length=1, max_length=128, description="The analysis these elements belong to."
    )
    nodes: list[GraphNode] = Field(default_factory=list, description="Graph nodes; empty when there is nothing to show.")
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="Graph relationships; empty when no relationship exists (the UI shows an empty state).",
    )
