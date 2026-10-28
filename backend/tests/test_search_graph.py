"""R7 — knowledge graph for historical text search.

These tests assert the graph comes from *real retrieved records*: every
node has a stable id, every edge references existing nodes, every edge
carries evidence, and nothing is invented to fill the canvas.
"""

from __future__ import annotations

import asyncio

import pytest


def _insert_records(url: str, records: list[dict]) -> None:
    from app.db.session import create_engine, create_session_factory, session_scope
    from app.repositories.record_repository import seed_records

    async def _run() -> None:
        engine = create_engine(url)
        try:
            async with session_scope(create_session_factory(engine)) as session:
                await seed_records(session, records)
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_graph_contains_the_subject_and_the_retrieved_records(auth_client):
    response = auth_client.get("/api/search/graph", params={"query": "Ada Lovelace"})
    assert response.status_code == 200

    body = response.json()
    ids = {node["id"] for node in body["nodes"]}
    labels = {node["label"] for node in body["nodes"]}

    subject = [node for node in body["nodes"] if node["id"].startswith("subject:")]
    assert len(subject) == 1
    assert subject[0]["id"] == "subject:ada lovelace"
    assert "Ada Lovelace" in labels
    # One node per retrieved record, with the record's own id.
    assert "rec-ada-lovelace" in ids
    assert body["sources"], "every record node is backed by its source"
    assert body["edges"], "the retrieval edges are shown"


def test_every_edge_references_an_existing_node_and_carries_evidence(auth_client):
    body = auth_client.get(
        "/api/search/graph", params={"query": "Ada Lovelace"}
    ).json()

    ids = {node["id"] for node in body["nodes"]}
    edge_ids = set()
    for edge in body["edges"]:
        assert edge["source"] in ids
        assert edge["target"] in ids
        assert edge["id"] not in edge_ids
        edge_ids.add(edge["id"])
        assert edge["evidence"], "an edge without evidence would be fabricated"
        assert edge["evidence"][0]["source"]
        # Nothing is presented as verified by retrieval alone.
        assert edge["verificationStatus"] == "unverified"


def test_graph_reports_limited_relationship_data_honestly(auth_client, auth_url):
    """A single retrieved record means no cross-record relationship exists,
    and the response says so instead of inventing one."""
    _insert_records(
        auth_url,
        [
            {
                "id": "rec-test-solo",
                "title": "Zzzq unique solo artifact",
                "year": "1901",
                "source": "Test archive",
                "url": "https://example.org/solo",
                "content": "A singular carved stone with no other subject named.",
            }
        ],
    )

    body = auth_client.get(
        "/api/search/graph", params={"query": "Zzzq unique solo artifact"}
    ).json()

    record_nodes = [n for n in body["nodes"] if not n["id"].startswith("subject:")]
    assert len(record_nodes) == 1
    assert any("only one record" in note.lower() for note in body["limitations"])
    assert all(not edge["id"].startswith("comention:") for edge in body["edges"])


def test_seeded_co_mentions_produce_supported_edges(auth_client):
    """The curated records genuinely name each other — those are the edges."""
    body = auth_client.get("/api/search/graph", params={"query": "Ada Lovelace"}).json()

    co_mentions = [e for e in body["edges"] if e["id"].startswith("comention:")]
    assert co_mentions
    for edge in co_mentions:
        assert edge["evidence"][0]["excerpt"], "every edge quotes its source text"
        assert edge["label"].startswith("mentions ")
        # No edge exists unless a record's own text contains the title.
        mentioning = next(n for n in body["nodes"] if n["id"] == edge["source"])
        mentioned = next(n for n in body["nodes"] if n["id"] == edge["target"])
        assert mentioned["label"] in edge["label"]
        assert mentioning["id"] != mentioned["id"]


def test_graph_without_any_retrieval_is_empty_and_explained(auth_client):
    body = auth_client.get(
        "/api/search/graph", params={"query": "quantum submarine expedition"}
    ).json()

    assert body["nodes"] == []
    assert body["edges"] == []
    assert body["sources"] == []
    assert any("no graph" in note.lower() for note in body["limitations"])


def test_co_mention_edge_comes_from_the_record_text(auth_client, auth_url):
    """A record naming another retrieved record's title yields a real edge."""
    _insert_records(
        auth_url,
        [
            {
                "id": "rec-test-marriage",
                "title": "Babbage Lovelace collaboration",
                "year": "1843",
                "source": "Test archive",
                "url": "https://example.org/collaboration",
                "content": (
                    "The collaboration between Charles Babbage and Ada Lovelace "
                    "produced the notes that described the Analytical Engine."
                ),
            },
            {
                "id": "rec-test-babbage-note",
                "title": "Charles Babbage",
                "year": "1871",
                "source": "Test archive",
                "url": "https://example.org/babbage",
                "content": "Charles Babbage designed the Difference Engine.",
            },
        ],
    )

    body = auth_client.get(
        "/api/search/graph", params={"query": "Babbage Lovelace collaboration"}
    ).json()

    co_mentions = [edge for edge in body["edges"] if edge["id"].startswith("comention:")]
    matching = [
        edge
        for edge in co_mentions
        if edge["source"] == "rec-test-marriage"
        and edge["target"] == "rec-test-babbage-note"
    ]
    assert matching, "the mentioning record must point at the record it names"
    edge = matching[0]
    assert "Charles Babbage" in edge["label"]
    assert "collaboration" in edge["evidence"][0]["excerpt"].lower()
    ids = {node["id"] for node in body["nodes"]}
    assert {edge["source"], edge["target"]} <= ids


def test_graph_query_validation_matches_search(auth_client):
    short = auth_client.get("/api/search/graph", params={"query": "A"})
    missing = auth_client.get("/api/search/graph")

    assert short.status_code == 422
    assert short.json()["error"]["code"] == "validation_error"
    assert missing.status_code == 422


def test_graph_and_search_share_the_same_retrieval(auth_client):
    query = "Analytical Engine"
    search = auth_client.get("/api/search", params={"query": query}).json()
    graph = auth_client.get("/api/search/graph", params={"query": query}).json()

    result_ids = {item["id"] for item in search["results"]}
    node_ids = {
        node["id"] for node in graph["nodes"] if not node["id"].startswith("subject:")
    }
    assert node_ids <= result_ids
    assert node_ids, "at least the search results become record nodes"
