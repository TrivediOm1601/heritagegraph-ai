"""B2 tests: the generated OpenAPI documentation *is* the shared contract.

These assertions compare the generated spec against
``frontend/src/types/api.ts`` so both members keep the same endpoint and
field names.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.analysis import (
    ACCEPTED_IMAGE_TYPES,
    ANALYSIS_FILE_FIELD,
    MAX_UPLOAD_BYTES,
    AnalysisResponse,
    SubmitAnalysisResponse,
)
from app.schemas.evidence import EvidenceResponse
from app.schemas.graph import GraphResponse
from app.schemas.search import SearchResponse

CONTRACT_PATHS = {
    "/api/health",
    "/api/ready",
    "/api/analysis",
    "/api/analysis/{analysis_id}",
    "/api/search",
    "/api/analysis/{analysis_id}/graph",
    "/api/analysis/{analysis_id}/relationships/{relationship_id}/evidence",
}


def test_openapi_documents_every_contract_endpoint(client):
    paths = set(client.get("/openapi.json").json()["paths"])

    assert CONTRACT_PATHS <= paths


def test_post_analysis_documents_success_and_error_statuses(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/analysis"]["post"]
    codes = set(operation["responses"])

    assert {"202", "413", "415", "422", "503"} <= codes
    # Every documented error response points at the canonical envelope.
    for code in ("413", "415", "422", "503"):
        assert "ErrorBody" in str(operation["responses"][code])


def test_operation_statuses_for_the_read_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert {"200", "404", "422", "503"} <= set(paths["/api/analysis/{analysis_id}"]["get"]["responses"])
    assert {"200", "422", "503"} <= set(paths["/api/search"]["get"]["responses"])
    assert {"200", "404", "422", "503"} <= set(
        paths["/api/analysis/{analysis_id}/graph"]["get"]["responses"]
    )
    assert {"200", "404", "422", "503"} <= set(
        paths["/api/analysis/{analysis_id}/relationships/{relationship_id}/evidence"]["get"]["responses"]
    )


def test_response_schemas_use_the_frontend_field_names(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert set(schemas["SubmitAnalysisResponse"]["properties"]) == {"analysisId", "status"}
    assert set(schemas["AnalysisResponse"]["properties"]) == {
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
    assert set(schemas["GraphResponse"]["properties"]) == {"analysisId", "nodes", "edges"}
    assert "verificationStatus" in schemas["GraphEdge"]["properties"]
    assert {"id", "label", "type", "position"} == set(schemas["GraphNode"]["properties"])
    assert set(schemas["EvidenceResponse"]["properties"]) == {
        "relationshipId",
        "verificationStatus",
        "summary",
        "evidence",
        "notes",
    }
    assert set(schemas["SearchResponse"]["properties"]) == {
        "notice",
        "query",
        "results",
        "briefing",
    }
    assert set(schemas["HistoricalMatch"]["properties"]) == {
        "id",
        "title",
        "year",
        "score",
        "source",
        "url",
        "excerpt",
        "relevance",
    }
    assert set(schemas["HistoricalBriefing"]["properties"]) == {
        "query",
        "queryKind",
        "method",
        "summary",
        "explanation",
        "keyFacts",
        "timeline",
        "people",
        "related",
        # Repair R6 — clickable related topics with identity + imagery.
        "relatedTopics",
        # Repair R6 — the subject's own profile (type, description, photo).
        "subject",
        # Repair R6 — optional generated summary, only when it cites the
        # quoted statements; absent otherwise.
        "synthesis",
        "sources",
        "uncertainty",
    }
    # The new sub-shapes use the same camelCase wire naming as the rest of
    # the contract, so the frontend needs no translation layer.
    assert set(schemas["SubjectImage"]["properties"]) == {
        "url",
        "thumbnailUrl",
        "originalUrl",
        "altText",
        "caption",
        "credit",
        "license",
        "licenseUrl",
        "sourcePageUrl",
        "filePageUrl",
    }
    assert set(schemas["SubjectProfile"]["properties"]) == {
        "name",
        "type",
        "description",
        "sourceUrl",
        "image",
    }
    assert set(schemas["RelatedTopic"]["properties"]) == {
        "id",
        "name",
        "type",
        "description",
        "query",
        "sourceUrl",
        "relevance",
        "image",
    }
    assert set(schemas["BriefingSynthesis"]["properties"]) == {
        "provider",
        "model",
        "method",
        "text",
        "sourceIndexes",
    }
    assert set(schemas["TimelineEntry"]["properties"]) == {
        "date",
        "description",
        "sourceIndex",
    }
    assert set(schemas["AnalysisEntity"]["properties"]) == {"id", "name", "type", "confidence"}


def test_analysis_state_enum_matches_the_frontend(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert set(schemas["AnalysisResponse"]["properties"]["status"]["enum"]) == {
        "queued",
        "processing",
        "completed",
        # R7: useful-but-degraded runs are reported truthfully.
        "partial",
        "failed",
    }
    assert set(schemas["SubmitAnalysisResponse"]["properties"]["status"]["enum"]) == {
        "queued",
        "processing",
    }


def test_upload_part_uses_the_contract_field_name(client):
    spec = client.get("/openapi.json").json()
    body_schema_name, body_schema = next(
        (name, schema)
        for name, schema in spec["components"]["schemas"].items()
        if name.startswith("Body_") and "file" in schema.get("properties", {})
    )

    assert body_schema["properties"]["file"]["type"] == "string"
    assert "multipart/form-data" in str(spec["paths"]["/api/analysis"]["post"]["requestBody"])


def test_upload_contract_constants_match_the_frontend():
    # Mirrors frontend/src/services/api.ts (ANALYSIS_FILE_FIELD,
    # ACCEPTED_IMAGE_TYPES, MAX_IMAGE_BYTES).
    assert ANALYSIS_FILE_FIELD == "file"
    assert MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    assert set(ACCEPTED_IMAGE_TYPES) == {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/tiff",
        "image/avif",
    }


def test_response_models_serialise_with_camel_case_over_the_wire():
    """End-to-end wire check: FastAPI's own serialiser, over a real request."""
    app = FastAPI()

    @app.get("/submit")
    def submit():
        return SubmitAnalysisResponse(analysis_id="an-1", status="queued")

    @app.get("/result")
    def result():
        return AnalysisResponse(id="an-1", status="completed", extracted_text="Ada")

    @app.get("/graph")
    def graph():
        return GraphResponse(analysis_id="an-1")

    @app.get("/evidence")
    def evidence():
        return EvidenceResponse(relationship_id="rel-9")

    @app.get("/search")
    def search():
        return SearchResponse(query="Ada", results=[])

    client = TestClient(app)
    assert set(client.get("/submit").json()) == {"analysisId", "status"}
    assert "extractedText" in client.get("/result").json()
    assert "analysisId" in client.get("/graph").json()
    assert "relationshipId" in client.get("/evidence").json()
    assert client.get("/search").json() == {
        "query": "Ada",
        "results": [],
        "briefing": None,
        "notice": None,
    }


def test_error_schema_matches_the_agreed_envelope(client):
    """OpenAPI must document the agreed error format:
    {"error": {code, message, request_id, details?}}."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert set(schemas["ErrorBody"]["properties"]) == {"error"}
    error_props = set(schemas["ErrorObject"]["properties"])
    assert {"code", "message", "request_id", "details"} == error_props
