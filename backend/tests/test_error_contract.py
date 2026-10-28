"""B2/B5 tests: the agreed error envelope on every failure path.

Agreed format (audit §3):

    {"error": {"code": ..., "message": ..., "request_id": ...}}
"""

import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_unknown_route_returns_canonical_envelope(client):
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip()
    assert body["error"]["code"] == "not_found"


def test_missing_query_returns_canonical_validation_envelope(client):
    response = client.get("/api/search")

    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["details"], list) and body["error"]["details"]
    detail = body["error"]["details"][0]
    assert {"loc", "msg", "type"} <= set(detail)
    assert any("query" in part for part in detail["loc"])


def test_error_payload_follows_the_agreed_format():
    from app.core.errors import DependencyUnavailableError, error_payload

    payload = error_payload("something failed", "dependency_unavailable", request_id="rid-1")
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert error["code"] == "dependency_unavailable"
    assert error["message"] == "something failed"
    assert error["request_id"] == "rid-1"

    # No request id available (e.g. constructed outside a request) -> key omitted,
    # never a fabricated value.
    assert "request_id" not in error_payload("x", "bad_request")

    exc = DependencyUnavailableError("pipeline down")
    assert exc.status_code == 503
    assert exc.code == "dependency_unavailable"
    assert exc.to_payload()["error"]["message"] == "pipeline down"


def test_error_body_follows_the_agreed_format_over_http(client):
    response = client.get("/api/search")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert {"code", "message", "request_id", "details"} <= set(error)
    uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    assert re.fullmatch(uuid_pattern, error["request_id"])
    # The header carries the same id for log correlation.
    assert response.headers["X-Request-Id"] == error["request_id"]


def test_request_id_is_unique_per_request(client):
    first = client.get("/api/search").json()["error"]["request_id"]
    second = client.get("/api/search").json()["error"]["request_id"]

    assert first != second


def test_success_responses_carry_the_request_id_header(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert re.fullmatch(
        r"[0-9a-f-]{36}", response.headers.get("X-Request-Id", "")
    )


def test_unhandled_exception_returns_canonical_500_without_internals():
    app = create_app(Settings.from_env({}))

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret-token-xyz")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    error = body["error"]
    assert error["code"] == "internal_error"
    assert isinstance(error["message"], str) and error["message"].strip()
    assert "secret-token-xyz" not in response.text
    assert "Traceback" not in response.text
    # Correlatable with the server-side traceback log line.
    assert re.fullmatch(r"[0-9a-f-]{36}", error["request_id"])
