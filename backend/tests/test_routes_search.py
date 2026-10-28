"""B2 tests: the search route."""

import pytest


def test_missing_query_is_422(client):
    response = client.get("/api/search")

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert any("query" in part for d in body["error"]["details"] for part in d["loc"])


def test_too_short_query_is_422(client):
    response = client.get("/api/search", params={"query": "A"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_whitespace_only_query_is_422_like_the_browser_guard(client):
    # Two spaces satisfy the raw min length but not the trimmed one —
    # the frontend trims first, so the backend must too.
    response = client.get("/api/search", params={"query": "  "})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert "at least 2 characters" in body["error"]["message"]


def test_overlong_query_is_422(client):
    response = client.get("/api/search", params={"query": "x" * 201})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_valid_query_reports_the_missing_dependency_not_an_empty_result(client):
    response = client.get("/api/search", params={"query": "Ada Lovelace"})

    # An unwired search must never answer 200 with [] — that would read
    # as "no records matched".
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "dependency_unavailable"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip()
