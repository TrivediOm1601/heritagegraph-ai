"""Integration-audit tests: B4 read routes backed by a real Module C database.

The existing route tests (``test_routes_search.py``,
``test_routes_graph_evidence.py``, ``test_api.py``) verify the *no
database* degradation — a valid request must surface the canonical 503,
never a fake success. This file verifies the other half of the contract:

* a migrated + seeded database serves **real data** through
  ``GET /api/search``, ``GET .../graph`` and ``GET .../evidence`` with
  field names exactly as the frozen schema (and Module A) expects;
* unknown resources are ``404`` in the canonical envelope;
* with the database still unconfigured, the same routes keep answering
  ``503 dependency_unavailable`` — errors stay distinguishable from
  empty results.

Everything runs in-process against the real FastAPI app and a real
SQLite database migrated by the real Alembic pipeline (no PostgreSQL
server exists in this workspace — same substitution as Module C's own
suite). The analysis row and its evidence are created through Module C's
repositories (including the real evidence evaluation), because B3/Module
D — the only producers reachable over HTTP — are not implemented yet.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.db.init_db import alembic_config
from app.db.session import (
    create_engine,
    create_session_factory,
    reset_default_engine,
    session_scope,
)
from app.main import create_app

RUN_ID = "run-wired"
OTHER_RUN_ID = "run-other"


#: Account the wired fixtures own their analyses with (R7 requires an
#: owner for every analysis read).
OWNER_EMAIL = "wired.owner@example.org"
OWNER_PASSWORD = "wired owner passphrase"


async def _seed(url: str) -> str:
    """Create a migrated-DB dataset through the real repositories and
    return the relationship id (created and evaluated for real)."""
    from app.repositories.analysis_repository import (
        attach_entities,
        create_analysis_run,
    )
    from app.repositories.user_repository import create_user
    from app.services.auth_service import hash_password
    from app.repositories.entity_repository import upsert_entities
    from app.repositories.record_repository import load_seed_records, seed_records
    from app.repositories.relationship_repository import save_relationships
    from app.services.evidence_service import evaluate_relationship_evidence

    engine = create_engine(url)
    sessions = create_session_factory(engine)
    try:
        async with session_scope(sessions) as session:
            await seed_records(session, load_seed_records())
            owner = await create_user(
                session,
                email=OWNER_EMAIL,
                password_hash=hash_password(OWNER_PASSWORD),
            )
            await create_analysis_run(
                session,
                analysis_id=RUN_ID,
                original_filename="plaque.png",
                content_type="image/png",
                user_id=owner.id,
            )
            await create_analysis_run(session, analysis_id=OTHER_RUN_ID, user_id=owner.id)
            entities = await upsert_entities(
                session,
                [
                    {"name": "Ada Lovelace", "entity_type": "person"},
                    {"name": "Analytical Engine", "entity_type": "event"},
                ],
            )
            await attach_entities(
                session,
                RUN_ID,
                [{"entity_id": entity.id, "confidence": 0.9} for entity in entities],
            )
            (relationship,) = await save_relationships(
                session,
                RUN_ID,
                [
                    {
                        "source": entities[0].id,
                        "target": entities[1].id,
                        "label": "wrote notes for",
                    }
                ],
            )
            # Real Module C evaluation: persists evidence + status.
            await evaluate_relationship_evidence(session, relationship.id)
            return relationship.id
    finally:
        await engine.dispose()


@pytest.fixture
def relationship_id(tmp_path, monkeypatch) -> str:
    """Migrated+seeded DATABASE_URL in env, app built, caches reset."""
    url = f"sqlite:///{tmp_path / 'wired.db'}"
    command.upgrade(alembic_config(url), "head")
    rel_id = asyncio.run(_seed(url))

    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings_cache()
    reset_default_engine()
    yield rel_id

    reset_settings_cache()
    reset_default_engine()


@pytest.fixture
def wired_client(relationship_id) -> TestClient:
    # ``Settings.from_env()`` (not ``{}``) so the app sees the fixture's
    # DATABASE_URL — authentication needs the database.
    client = TestClient(create_app(Settings.from_env()))
    logged_in = client.post(
        "/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    client.relationship_id = relationship_id  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# Workflow 2 — historical search over real records
# ---------------------------------------------------------------------------


def test_search_returns_real_records_in_the_contract_shape(wired_client):
    response = wired_client.get("/api/search", params={"query": "Ada Lovelace"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "ada lovelace"  # normalised echo
    assert body["results"], "the curated seed must match this query"
    top = body["results"][0]
    assert top["id"] == "rec-ada-lovelace"
    assert top["title"] and top["source"]  # traceable source on the wire
    assert 0 <= top["score"] <= 1
    # Exactly the contract's field set — nothing the frontend never gets.
    assert set(top) <= {
        "id",
        "title",
        "year",
        "score",
        "source",
        "url",
        "excerpt",
        "relevance",
    }
    assert top["url"].lower().startswith("https://")  # R3: source link persisted
    # R5: the response also *explains* the material, not just links to it.
    assert top["excerpt"], "the match must carry the record's own text"
    assert top["relevance"] in {"high", "medium", "low"}


def test_search_returns_200_empty_results_when_nothing_matches(wired_client):
    response = wired_client.get(
        "/api/search", params={"query": "quantum submarine expedition"}
    )

    assert response.status_code == 200
    # Nothing matched → empty results AND no explanation to show; this is
    # still a success (a failure is a 503, never an empty list).
    assert response.json() == {
        "query": "quantum submarine expedition",
        "results": [],
        "briefing": None,
        # No notice: the (disabled or healthy) live source is not the reason
        # this search came back empty.
        "notice": None,
    }


def test_search_returns_a_grounded_briefing_not_just_source_titles(wired_client):
    """R5/R6: the explanation comes from the retrieved records' own text,
    cites them, and never asserts anything they do not contain."""
    body = wired_client.get("/api/search", params={"query": "Ada Lovelace"}).json()

    briefing = body["briefing"]
    assert briefing is not None
    assert briefing["method"] == "extractive-quotes"
    assert briefing["summary"], "the answer needs a summary, not just links"
    assert briefing["sources"], "every statement must be attributable"
    assert briefing["sources"][0]["index"] == 1
    citations = {fact["sourceIndex"] for fact in briefing["keyFacts"]}
    assert citations <= {source["index"] for source in briefing["sources"]}
    # Offline (no HG_LIVE_SOURCES in tests) the briefing must say so.
    assert any("HG_LIVE_SOURCES" in note for note in briefing["uncertainty"])


def test_search_validation_and_failure_modes_stay_distinct(wired_client):
    # 422: too short (validation, not failure, not empty)
    short = wired_client.get("/api/search", params={"query": "A"})
    assert short.status_code == 422
    assert short.json()["error"]["code"] == "validation_error"


def test_graph_requires_authentication(relationship_id):
    """A fresh client with no session cookie is a 401 — never data."""
    anonymous = TestClient(create_app(Settings.from_env()))
    response = anonymous.get(f"/api/analysis/{RUN_ID}/graph")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_search_without_database_is_503_not_empty(monkeypatch):
    """Error-vs-empty distinction: no DATABASE_URL → 503, never 200 []."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_settings_cache()
    reset_default_engine()
    try:
        client = TestClient(create_app(Settings.from_env({})))
        response = client.get("/api/search", params={"query": "Ada Lovelace"})
    finally:
        reset_settings_cache()
        reset_default_engine()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"


# ---------------------------------------------------------------------------
# Workflow 3 — graph and evidence over persisted relationships
# ---------------------------------------------------------------------------


def test_graph_returns_persisted_nodes_and_edges(wired_client):
    response = wired_client.get(f"/api/analysis/{RUN_ID}/graph")

    assert response.status_code == 200
    body = response.json()
    assert body["analysisId"] == RUN_ID  # camelCase on the wire
    assert {node["label"] for node in body["nodes"]} == {
        "Ada Lovelace",
        "Analytical Engine",
    }
    for node in body["nodes"]:
        assert set(node) == {"id", "label", "type", "position"}
        assert node["position"] is None  # no fabricated layout

    (edge,) = body["edges"]
    assert set(edge) == {"id", "source", "target", "label", "verificationStatus"}
    assert edge["label"] == "wrote notes for"
    # The real Module C evaluation outcome — persisted, not invented.
    assert edge["verificationStatus"] == "verified"
    assert edge["source"] in {node["id"] for node in body["nodes"]}
    assert edge["target"] in {node["id"] for node in body["nodes"]}


def test_graph_for_unknown_analysis_is_404(wired_client):
    response = wired_client.get("/api/analysis/no-such-run/graph")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_evidence_returns_the_correct_relationship_payload(wired_client):
    relationship_id = wired_client.relationship_id  # type: ignore[attr-defined]
    graph = wired_client.get(f"/api/analysis/{RUN_ID}/graph").json()
    (edge,) = graph["edges"]
    assert edge["id"] == relationship_id

    response = wired_client.get(
        f"/api/analysis/{RUN_ID}/relationships/{relationship_id}/evidence"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["relationshipId"] == relationship_id  # correct relationship id
    assert body["verificationStatus"] == "verified"  # backend's actual status
    assert body["summary"]
    assert body["evidence"], "the evaluation persisted evidence rows"
    item = body["evidence"][0]
    assert set(item) == {"id", "source", "excerpt", "url"}
    assert item["source"] and "Ada Lovelace" in (item["excerpt"] or "")
    # R3: the cited record's URL reaches the panel; records without one
    # still return null — a link is never invented.
    assert item["url"].lower().startswith("https://")


def test_evidence_for_unknown_relationship_is_404(wired_client):
    response = wired_client.get(
        f"/api/analysis/{RUN_ID}/relationships/no-such-rel/evidence"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_evidence_cannot_be_read_across_analyses(wired_client):
    """An edge of RUN_ID requested under OTHER_RUN_ID is a 404, not a leak."""
    relationship_id = wired_client.relationship_id  # type: ignore[attr-defined]
    response = wired_client.get(
        f"/api/analysis/{OTHER_RUN_ID}/relationships/{relationship_id}/evidence"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_graph_for_analysis_without_relationships_is_an_empty_graph(wired_client):
    response = wired_client.get(f"/api/analysis/{OTHER_RUN_ID}/graph")

    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == [] and body["edges"] == []  # empty state, not an error


# ---------------------------------------------------------------------------
# Repair R6 — an unreachable live source must not look like "nothing matched"
# ---------------------------------------------------------------------------


def test_a_rate_limited_live_source_is_reported_as_a_notice(wired_client, monkeypatch):
    """A source outage degrades the *answer*, never the request — and it is
    said out loud instead of silently reading as an empty archive."""
    monkeypatch.setenv("HG_LIVE_SOURCES", "1")

    async def failing_search(query, *, limit=5, stats=None, **_):
        if stats is not None:
            stats["unavailable"] = stats.get("unavailable", 0) + 1
        return []

    monkeypatch.setattr(
        "app.services.retrieval_service.search_wikipedia", failing_search
    )

    response = wired_client.get(
        "/api/search", params={"query": "quantum submarine expedition"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["notice"] and "could not be reached or rate-limited" in body["notice"]


def test_a_healthy_source_sets_no_notice(wired_client, monkeypatch):
    monkeypatch.setenv("HG_LIVE_SOURCES", "1")

    async def failing_search(query, *, limit=5, stats=None, **_):
        if stats is not None:
            stats["ok"] = stats.get("ok", 0) + 1
        return []

    monkeypatch.setattr(
        "app.services.retrieval_service.search_wikipedia", failing_search
    )

    response = wired_client.get(
        "/api/search", params={"query": "quantum submarine expedition"}
    )

    assert response.status_code == 200
    assert response.json()["notice"] is None
