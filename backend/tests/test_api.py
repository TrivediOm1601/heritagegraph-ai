"""B5 — API integration tests and module boundaries.

Two runnable layers:

* **Module boundary** (in-process ``client``): without configuration,
  every contract endpoint must degrade to the canonical 503 envelope —
  never a 500, never a fake success — and a valid upload must be refused
  with an actionable message instead of a job nothing could process.
* **End-to-end** (``live_server``/``live_client``, ``@pytest.mark
  .integration``): a real uvicorn process with a real migrated+seeded
  database and ``HG_DEMO_MODE=1`` (fixture providers — no credentials
  exist in this workspace, INT-005): the full upload → background
  processing → poll → graph → evidence journey over real sockets.

Behaviours that genuinely cannot run here are kept as skipped tests
whose ``reason`` starts with an ``INT-nn`` id defined in
backend/INTEGRATION_ISSUES.md (each assigned to an owner). They are
deliberately neither deleted nor faked.
"""

import time

import pytest

from conftest import SAMPLE_PNG

CONTRACT_PATHS = {
    "/api/health",
    "/api/ready",
    "/api/analysis",
    "/api/analysis/{analysis_id}",
    "/api/search",
    "/api/analysis/{analysis_id}/graph",
    "/api/analysis/{analysis_id}/relationships/{relationship_id}/evidence",
}


# ---------------------------------------------------------------------------
# Module boundary (in-process)
# ---------------------------------------------------------------------------


def test_get_analysis_reports_the_missing_database(client):
    response = client.get("/api/analysis/an-123")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


def test_missing_provider_credentials_are_reported_actionably(auth_client, sample_image):
    """INT-005 boundary: with no credentials and no demo flag, a valid
    upload is refused with a safe message naming exactly the variables
    required to run — no run is queued that could not possibly process.

    Repair R1: VISION_* is mandatory (it produces the entities); OCR is
    an optional channel, so its variables are *not* demanded when the
    whole channel is absent.
    """
    import io

    response = auth_client.post(
        "/api/analysis",
        files={"file": ("photo.png", io.BytesIO(sample_image), "image/png")},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    message = body["error"]["message"]
    for variable in (
        "VISION_API_BASE_URL",
        "VISION_API_KEY",
        "VISION_MODEL",
    ):
        assert variable in message
    assert "Traceback" not in response.text


def test_half_configured_ocr_is_reported_as_misconfiguration(auth_client, sample_image, monkeypatch):
    """R1: OCR is optional as a channel, but a *partial* OCR config is a
    typo that must be named — not silently ignored."""
    import io

    monkeypatch.setenv("OCR_API_URL", "https://ocr.example.test/read")
    # OCR_API_KEY deliberately missing; VISION_* still fully missing too.

    response = auth_client.post(
        "/api/analysis",
        files={"file": ("photo.png", io.BytesIO(sample_image), "image/png")},
    )

    assert response.status_code == 503
    message = response.json()["error"]["message"]
    assert "OCR_API_KEY" in message
    assert "partially configured" in message


@pytest.mark.parametrize("bad_id", ["%20", "a%20b"])
def test_analysis_id_validation_is_enforced(auth_client, bad_id):
    # Authenticated: path validation is reached only after the session gate
    # (an anonymous request is a 401, which is asserted elsewhere).
    response = auth_client.get(f"/api/analysis/{bad_id}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_every_contract_endpoint_degrades_to_the_canonical_503(client, sample_image):
    """The integration boundary: a missing Module C/D dependency must surface
    as the same machine-readable envelope everywhere, with a human message
    and no internal detail."""
    import io

    calls = {
        "POST /api/analysis": client.post(
            "/api/analysis",
            files={"file": ("sample.png", io.BytesIO(sample_image), "image/png")},
        ),
        "GET /api/analysis/{id}": client.get("/api/analysis/an-1"),
        "GET /api/search": client.get("/api/search", params={"query": "Ada Lovelace"}),
        "GET graph": client.get("/api/analysis/an-1/graph"),
        "GET evidence": client.get("/api/analysis/an-1/relationships/rel-9/evidence"),
    }

    for name, response in calls.items():
        assert response.status_code == 503, f"{name} -> {response.status_code}"
        body = response.json()
        assert body["error"]["code"] == "dependency_unavailable", name
        assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip(), name
        assert "Traceback" not in response.text, name
        assert "File \"" not in response.text, name


# ---------------------------------------------------------------------------
# End-to-end over a real uvicorn process
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def completed_analysis(live_client):
    """One demo-mode analysis driven to ``completed`` over real HTTP.

    Shared by the journey/graph/evidence tests so the full pipeline
    (B3 → D3/D4 → C3/C4 → persistence) executes exactly once against
    the live server's seeded database. Uses the module-level SAMPLE_PNG
    (the fixture-scoped ``sample_image`` cannot cross this scope).
    """
    submitted = live_client.post(
        "/api/analysis",
        files={"file": ("certificate.png", SAMPLE_PNG, "image/png")},
    )
    assert submitted.status_code == 202, submitted.text
    analysis_id = submitted.json()["analysisId"]

    deadline = time.monotonic() + 60
    payload: dict = {}
    while time.monotonic() < deadline:
        response = live_client.get(f"/api/analysis/{analysis_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            break
        time.sleep(0.2)
    assert payload["status"] == "completed", payload.get("error") or payload
    return payload


@pytest.mark.integration
class TestEndToEndHttp:
    def test_health_over_http(self, live_client):
        response = live_client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "HeritageGraph AI",
            "environment": "development",
        }

    def test_ready_reports_the_provisioned_database(self, live_client):
        # The live process runs against its own migrated + seeded database
        # (conftest provisions it before startup), so readiness must
        # actually ping that database, not just the configuration.
        health = live_client.get("/api/health")
        ready = live_client.get("/api/ready")

        assert health.status_code == 200
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["checks"]["configuration"]["ok"] is True
        assert body["checks"]["database"]["ok"] is True

    def test_upload_is_accepted_and_immediately_pollable(self, live_client, sample_image):
        response = live_client.post(
            "/api/analysis",
            files={"file": ("document.png", sample_image, "image/png")},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 202
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert set(body) == {"analysisId", "status"}
        assert body["status"] in {"queued", "processing"}

        # The run row exists before the 202 leaves, so the id polls 200.
        polled = live_client.get(f"/api/analysis/{body['analysisId']}")
        assert polled.status_code == 200
        payload = polled.json()
        assert payload["id"] == body["analysisId"]
        assert payload["status"] in {"queued", "processing", "completed", "failed"}

    def test_analysis_journey_completes_with_extraction_and_matches(
        self, completed_analysis
    ):
        """Workflow 2 end to end: extraction, entities, seeded matches —
        with demo mode labelling its fixture output as such."""
        assert completed_analysis["error"] is None

        text = completed_analysis["extractedText"]
        assert isinstance(text, str) and text.strip()

        entities = completed_analysis["entities"] or []
        assert entities, "fixture vision output must yield entities"
        for entity in entities:
            assert {"id", "name", "type"} <= set(entity)

        matches = completed_analysis["matches"] or []
        assert matches, "retrieval must match the seeded record store"
        for match in matches:
            assert {"id", "title", "score", "source"} <= set(match)
            assert str(match["source"]).strip(), "every match cites a source"

        # R5: the analysis also explains the material it matched, quoting the
        # records it retrieved — not just a list of link titles.
        briefing = completed_analysis["briefing"]
        assert briefing is not None, "a completed analysis must carry a briefing"
        assert briefing["method"] == "extractive-quotes"
        assert briefing["summary"], "the briefing needs an explanation"
        assert briefing["sources"], "every briefing statement is attributable"
        assert {source["index"] for source in briefing["sources"]} >= {1}
        assert briefing["query"], "the briefing names what it is about"

        warnings = completed_analysis["warnings"] or []
        assert any("DEMO MODE" in warning for warning in warnings), (
            "fixture-based output must be labelled as demo output"
        )

    def test_invalid_uploads_are_rejected_over_http(self, live_client, sample_image):
        unsupported = live_client.post(
            "/api/analysis",
            files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        )
        wrong_field = live_client.post(
            "/api/analysis",
            files={"not_file": ("doc.png", sample_image, "image/png")},
        )

        assert unsupported.status_code == 415
        assert unsupported.json()["error"]["code"] == "unsupported_media_type"
        assert wrong_field.status_code == 422
        assert wrong_field.json()["error"]["code"] == "validation_error"
        assert any(
            "file" in part for d in wrong_field.json()["error"]["details"] for part in d["loc"]
        )

    def test_search_contract_over_http(self, live_client):
        valid = live_client.get("/api/search", params={"query": "Ada Lovelace"})
        unrelated = live_client.get("/api/search", params={"query": "zzzz-unfindable"})
        missing = live_client.get("/api/search")

        # Real records through Module C: results depend on the query and
        # every hit carries a traceable source.
        assert valid.status_code == 200
        body = valid.json()
        # The echo is the normalized query (casefolded + whitespace
        # collapsed) — the service's contract, not the raw parameter.
        assert body["query"] == "ada lovelace"
        assert body["results"], "a relevant query must return seeded records"
        for item in body["results"]:
            assert {"id", "title", "score", "source"} <= set(item)
            assert str(item["source"]).strip()

        # An unrelated query is a distinguishable, real empty result.
        assert unrelated.status_code == 200
        assert unrelated.json()["results"] == []

        # A missing query stays a validation error, never an empty success.
        assert missing.status_code == 422
        assert missing.json()["error"]["code"] == "validation_error"

    def test_graph_and_evidence_over_http(self, live_client, completed_analysis):
        """Workflows 3+4: the persisted graph, then evidence for the FIRST
        edge's real relationship id, then the missing-resource cases."""
        analysis_id = completed_analysis["id"]

        graph = live_client.get(f"/api/analysis/{analysis_id}/graph")
        assert graph.status_code == 200
        body = graph.json()
        assert body["analysisId"] == analysis_id
        node_ids = {node["id"] for node in body["nodes"]}
        assert body["nodes"] and body["edges"]
        for edge in body["edges"]:
            assert edge["source"] in node_ids, edge
            assert edge["target"] in node_ids, edge
            assert edge["verificationStatus"] in {
                "verified", "disputed", "unsupported", "unverified"
            }, edge

        first = body["edges"][0]
        evidence = live_client.get(
            f"/api/analysis/{analysis_id}/relationships/{first['id']}/evidence"
        )
        assert evidence.status_code == 200
        payload = evidence.json()
        assert payload["relationshipId"] == first["id"]
        # The panel and the edge must agree on the actual status.
        assert payload["verificationStatus"] == first["verificationStatus"]
        assert isinstance(payload["evidence"], list)

        # Missing resources are 404/422 — never a dependency-shaped 503.
        assert live_client.get("/api/analysis/an-1/graph").status_code == 404
        assert live_client.get("/api/analysis/%20/graph").status_code == 422
        assert (
            live_client.get(
                f"/api/analysis/{analysis_id}/relationships/rel-does-not-exist/evidence"
            ).status_code
            == 404
        )
        # Cross-analysis reads must not leak another run's evidence.
        assert (
            live_client.get("/api/analysis/an-1/relationships/rel-9/evidence").status_code
            == 404
        )

    def test_unknown_route_uses_the_canonical_envelope(self, live_client):
        response = live_client.get("/api/nope")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip()

    def test_error_bodies_never_leak_internals(self, live_client, sample_image):
        failing = [
            live_client.get("/api/does-not-exist"),
            live_client.get("/api/search"),
            live_client.post(
                "/api/analysis",
                files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
            ),
            live_client.post("/api/analysis", files={"file": ("a.png", b"", "image/png")}),
        ]

        for response in failing:
            assert response.status_code >= 400
            assert "Traceback" not in response.text
            assert "File \"" not in response.text
            assert "detail" not in response.json()  # FastAPI's default body
            assert isinstance(response.json()["error"]["message"], str)

    def test_openapi_is_served_over_http(self, live_client):
        response = live_client.get("/openapi.json")

        assert response.status_code == 200
        spec = response.json()
        assert CONTRACT_PATHS <= set(spec["paths"])
        assert "ErrorBody" in spec["components"]["schemas"]


# ---------------------------------------------------------------------------
# Blocked behaviours — skipped, tracked, assigned (INTEGRATION_ISSUES.md)
# ---------------------------------------------------------------------------
#
# INT-001 (search over the live server), INT-002 (upload → poll → graph →
# evidence) and INT-003 (evidence endpoint) now RUN in TestEndToEndHttp
# above, against the conftest-provisioned database. The two below stay
# skipped because the live process cannot be made to fail on demand;
# their executed equivalents are named in each reason.


@pytest.mark.skip(
    reason=(
        "INT-004 (owner: Module C): needs a live server whose database dies "
        "mid-request. Executed equivalents: "
        "test_routes_wired_db.py::test_search_without_database_is_503_not_empty "
        "and test_health.py::test_database_unavailable_fails_readiness_but_not_health."
    )
)
def test_database_failure_maps_to_a_503(live_client):
    response = live_client.get("/api/search", params={"query": "Ada"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


@pytest.mark.skip(
    reason=(
        "INT-005 (owner: Module D): needs live provider credentials, which do "
        "not exist in this workspace (the live server runs HG_DEMO_MODE=1). "
        "Executed equivalent: "
        "test_missing_provider_credentials_are_reported_actionably in this file."
    )
)
def test_provider_failure_maps_to_a_503(live_client):
    response = live_client.post(
        "/api/analysis", files={"file": ("doc.png", b"\x89PNG\r\n\x1a\n", "image/png")}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
