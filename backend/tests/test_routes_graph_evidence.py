"""B2 tests: the graph and evidence routes."""

import pytest


def test_graph_with_valid_id_reports_missing_dependency(client):
    response = client.get("/api/analysis/an-1/graph")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


@pytest.mark.parametrize("bad_id", ["%20", "a%20b"])
def test_graph_with_malformed_id_is_422(auth_client, bad_id):
    response = auth_client.get(f"/api/analysis/{bad_id}/graph")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_evidence_with_valid_ids_reports_missing_dependency(client):
    response = client.get("/api/analysis/an-1/relationships/rel-9/evidence")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


@pytest.mark.parametrize(
    "path",
    [
        "/api/analysis/%20/relationships/rel-9/evidence",
        "/api/analysis/an-1/relationships/%20/evidence",
    ],
)
def test_evidence_with_malformed_id_is_422(auth_client, path):
    response = auth_client.get(path)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
