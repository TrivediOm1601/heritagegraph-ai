"""R7 — personal analysis history: recording, dedupe, reopening, ownership."""

from __future__ import annotations

import io

from conftest import SAMPLE_PNG


def _upload(client, filename: str = "monument.png") -> str:
    response = client.post(
        "/api/analysis",
        files={"file": (filename, io.BytesIO(SAMPLE_PNG), "image/png")},
    )
    assert response.status_code == 202, response.text
    return response.json()["analysisId"]


def test_authenticated_text_search_is_recorded(auth_client):
    response = auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    assert response.status_code == 200

    history = auth_client.get("/api/history").json()
    assert history["total"] == 1
    entry = history["items"][0]
    assert entry["kind"] == "text_search"
    assert entry["query"] == "ada lovelace"
    assert entry["status"] == "completed"
    assert entry["summary"]
    assert entry["createdAt"] and entry["updatedAt"]


def test_repeating_a_search_does_not_duplicate_history(auth_client):
    for _ in range(3):
        auth_client.get("/api/search", params={"query": "Ada Lovelace"})

    history = auth_client.get("/api/history").json()
    assert history["total"] == 1


def test_different_searches_create_separate_entries(auth_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    auth_client.get("/api/search", params={"query": "Charles Babbage"})

    history = auth_client.get("/api/history").json()
    assert history["total"] == 2
    queries = {item["query"] for item in history["items"]}
    assert queries == {"ada lovelace", "charles babbage"}


def test_history_is_ordered_most_recent_first(auth_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    auth_client.get("/api/search", params={"query": "Charles Babbage"})

    items = auth_client.get("/api/history").json()["items"]
    assert items[0]["query"] == "charles babbage"


def test_image_analysis_creates_one_entry_and_updates_its_status(
    auth_client, monkeypatch
):
    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")

    analysis_id = _upload(auth_client)

    history = auth_client.get("/api/history").json()
    assert history["total"] == 1, "one upload must create exactly one row"
    entry = history["items"][0]
    assert entry["kind"] == "image_analysis"
    assert entry["analysisId"] == analysis_id
    # The run finished before the row was read (TestClient runs background
    # tasks inline), so the status snapshot advanced from "queued".
    assert entry["status"] in {"completed", "partial"}
    assert "Image analysis" in (entry["summary"] or "")


def test_history_entry_can_be_reopened_and_replayed(auth_client, monkeypatch):
    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")

    _upload(auth_client)
    entry_id = auth_client.get("/api/history").json()["items"][0]["id"]

    detail = auth_client.get(f"/api/history/{entry_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["entry"]["id"] == entry_id
    assert body["analysis"] is not None
    assert body["analysis"]["id"] == body["entry"]["analysisId"]
    assert body["graph"] is not None
    assert "nodes" in body["graph"] and "edges" in body["graph"]
    # The image itself is not stored; that limitation is stated, not hidden.
    assert any("image" in note.lower() for note in body["limitations"])


def test_text_search_entry_replays_its_stored_result(auth_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    entry_id = auth_client.get("/api/history").json()["items"][0]["id"]

    body = auth_client.get(f"/api/history/{entry_id}").json()

    assert body["search"] is not None
    assert body["search"]["query"] == "ada lovelace"
    assert body["search"]["results"], "the stored result must be replayable"
    assert body["analysis"] is None


def test_history_pagination_and_kind_filter(auth_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    auth_client.get("/api/search", params={"query": "Charles Babbage"})

    page = auth_client.get("/api/history", params={"limit": 1, "offset": 0}).json()
    assert len(page["items"]) == 1
    assert page["total"] == 2
    assert page["limit"] == 1

    text_only = auth_client.get("/api/history", params={"kind": "text_search"}).json()
    assert text_only["total"] == 2
    images_only = auth_client.get("/api/history", params={"kind": "image_analysis"}).json()
    assert images_only["total"] == 0


def test_failed_analysis_is_recorded_as_failed(auth_client, monkeypatch):
    """A run that cannot process must not masquerade as completed."""
    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")

    # Demo fixtures return a valid extraction, so force the failure at the
    # provider boundary instead: an unusable vision response every time.
    from app.services import analysis_service

    original = analysis_service.run_extraction

    async def _broken(*args, **kwargs):
        raise analysis_service.ProviderResponseError(
            "The vision model is unavailable.", provider="vision service"
        )

    monkeypatch.setattr(analysis_service, "run_extraction", _broken)
    try:
        _upload(auth_client)
    finally:
        monkeypatch.setattr(analysis_service, "run_extraction", original)

    entry = auth_client.get("/api/history").json()["items"][0]
    assert entry["status"] == "failed"
    assert entry["summary"]


def test_unknown_history_id_is_404(auth_client):
    response = auth_client.get("/api/history/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
