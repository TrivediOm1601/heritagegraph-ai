"""B2 tests: canonical schema validation and the wire format."""

import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisResponse, SubmitAnalysisResponse
from app.schemas.entity import AnalysisEntity
from app.schemas.evidence import EvidenceResponse
from app.schemas.graph import GraphEdge, GraphNode, GraphResponse
from app.schemas.search import HistoricalMatch, SearchResponse


def test_wire_keys_are_camel_case():
    """The JSON keys are exactly what frontend/src/types/api.ts declares."""
    submit = SubmitAnalysisResponse(analysis_id="an-1", status="queued")
    assert set(submit.model_dump(by_alias=True)) == {"analysisId", "status"}

    result = AnalysisResponse(
        id="an-1",
        status="completed",
        progress=0.5,
        extracted_text="Ada Lovelace",
        entities=[],
        matches=[],
        warnings=["low contrast"],
        error=None,
    )
    data = result.model_dump(by_alias=True)
    assert set(data) == {
        "id",
        "status",
        "progress",
        "extractedText",
        "interpretation",
        "entities",
        "matches",
        "briefing",
        "warnings",
        "error",
    }
    assert data["extractedText"] == "Ada Lovelace"
    assert "extracted_text" not in data

    graph = GraphResponse(
        analysis_id="an-1",
        nodes=[GraphNode(id="n1", label="Ada Lovelace")],
        edges=[GraphEdge(id="r1", source="n1", target="n2", verification_status="disputed")],
    )
    graph_data = graph.model_dump(by_alias=True, exclude_none=False)
    assert "analysisId" in graph_data
    assert graph_data["edges"][0]["verificationStatus"] == "disputed"
    assert "verification_status" not in graph_data["edges"][0]

    evidence = EvidenceResponse(relationship_id="r1")
    assert "relationshipId" in evidence.model_dump(by_alias=True)


def test_fields_can_be_constructed_by_alias_or_python_name():
    by_python_name = SubmitAnalysisResponse(analysis_id="an-1", status="queued")
    by_alias = SubmitAnalysisResponse(analysisId="an-1", status="queued")
    assert by_python_name.analysis_id == by_alias.analysis_id == "an-1"


@pytest.mark.parametrize(
    "build",
    [
        lambda: AnalysisEntity(id="", name="Ada", type="person"),
        lambda: AnalysisEntity(id="e1", name="", type="person"),
        lambda: AnalysisEntity(id="e1", name="Ada", type=""),
        lambda: AnalysisEntity(id="e1", name="Ada", type="person", confidence=1.5),
        lambda: AnalysisEntity(id="e1", name="Ada", type="person", confidence=-0.1),
        lambda: AnalysisResponse(id="an-1", status="pending"),
        lambda: AnalysisResponse(id="an-1", status="completed", progress=1.4),
        lambda: AnalysisResponse(id="", status="completed"),
        lambda: HistoricalMatch(id="m1", title="T", score=2.0),
        lambda: HistoricalMatch(id="", title="T"),
        lambda: SearchResponse(results=[]),
        lambda: GraphEdge(id="r1", source="", target="n2"),
        lambda: GraphNode(id="n1", label=""),
    ],
)
def test_invalid_payloads_are_rejected(build):
    with pytest.raises(ValidationError):
        build()


def test_valid_payloads_are_accepted():
    entity = AnalysisEntity(id="e1", name="Ada Lovelace", type="person", confidence=0.8)
    match = HistoricalMatch(
        id="m1", title="Analytical Engine", year="c. 1843", score=0.91, source="Archive"
    )
    result = AnalysisResponse(
        id="an-1",
        status="completed",
        progress=1.0,
        extracted_text="text",
        entities=[entity],
        matches=[match],
        warnings=[],
        error=None,
    )
    graph = GraphResponse(
        analysis_id="an-1",
        nodes=[GraphNode(id="n1", label="Ada", type="person", position={"x": 1.0, "y": 2.0})],
        edges=[GraphEdge(id="r1", source="n1", target="n2", label="collaborated with")],
    )
    evidence = EvidenceResponse(
        relationship_id="r1",
        verification_status="verified",
        summary="Two independent sources",
        evidence=[{"id": "ev1", "source": "Letter", "excerpt": "...", "url": "https://x"}],
        notes=["none"],
    )
    search = SearchResponse(query="Ada", results=[match])

    assert result.entities[0].name == "Ada Lovelace"
    assert graph.nodes[0].position.x == 1.0
    assert evidence.evidence[0].source == "Letter"
    assert search.results[0].score == 0.91


def test_absent_verification_status_is_never_verified():
    edge = GraphEdge(id="r1", source="n1", target="n2")
    assert edge.verification_status is None

    evidence = EvidenceResponse(relationship_id="r1")
    assert evidence.verification_status == "unverified"


def test_unknown_verification_status_is_passed_through():
    edge = GraphEdge(id="r1", source="n1", target="n2", verification_status="needs-review")
    assert edge.verification_status == "needs-review"
