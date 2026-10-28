"""D5 — integration example: Module D invoked through the agreed service interfaces.

Runs the exact call sequence B3's orchestration service
(``app.services.analysis_service`` — owned by Member 2 and **not yet
implemented**, see INT-002) must perform, against Module C's real
Alembic-migrated database and real seeded records:

1. ``prepare_image`` (D1) — a real generated document image
2. ``run_extraction`` (D2 + D3) — fixture provider transports, real
   parsing/validation/normalisation
3. ``seed_records`` + ``search_historical_records`` (C3) — real retrieval
4. ``generate_relationship_candidates`` (D4) — record provenance from
   the actual retrieval results
5. ``upsert_entities`` / ``attach_entities`` / ``save_relationships``
   (C2) — Module D's outputs persisted through Module C's repositories
6. ``evaluate_relationship_evidence`` (C4) — real verdicts + evidence
7. ``build_graph`` (D4) — ``GraphResponse`` with the evaluated statuses
8. ``GET /api/analysis/{id}/graph`` and
   ``.../relationships/rel-1/evidence`` (B4) — read back over HTTP

The only substituted layer is the provider transport (fixture
``httpx.MockTransport`` clients — no credentials exist yet, INT-005);
the database, migrations, repositories, retrieval, evidence evaluation
and HTTP layer are all real. A mismatch between Module D's outputs and
Module C's constraints (entity vocabulary, verification statuses,
label length, self-loops, unique pairs, id limits) would raise inside
step 5 and fail these tests.
"""

from __future__ import annotations

import asyncio

from alembic import command

# Shared processing fixtures (provider env, fixture clients, the real
# document image) — imported from D3's test module rather than copied.
from test_extraction import PROVIDER_ENV, _ocr, _vision, document_image

from app.core.config import Settings, reset_settings_cache
from app.db.init_db import alembic_config
from app.db.session import create_engine, create_session_factory, reset_default_engine, session_scope
from app.schemas.entity import AnalysisEntity
from app.schemas.graph import VERIFICATION_STATUSES, GraphResponse
from app.services.extraction_service import run_extraction
from app.services.graph_service import build_graph
from app.services.image_service import prepare_image
from app.services.relationship_service import generate_relationship_candidates

import pytest

RUN_ID = "run-d5"

OCR_TEXT = "Certificate of Ada Lovelace - Analytical Engine - Punch, London 1843"

#: A realistic model response: duplicate name, out-of-vocabulary type,
#: two resolvable relationships and one pointing at an unknown entity.
VISION_PAYLOAD = {
    "text": "A typed certificate naming Ada Lovelace, dated 1843.",
    "entities": [
        {"name": "Ada Lovelace", "type": "person", "confidence": 0.96},
        {"name": "ada lovelace", "type": "person", "confidence": 0.72},
        {"name": "Analytical Engine", "type": "event", "confidence": 0.91},
        {"name": "Punch", "type": "object", "confidence": 0.4},
    ],
    "relationships": [
        {"source": "Ada Lovelace", "target": "Analytical Engine",
         "label": "wrote notes for", "confidence": 0.9},
        {"source": "Ada Lovelace", "target": "Punch",
         "label": "appeared in", "confidence": 0.7},
        {"source": "Ghost Person", "target": "Ada Lovelace",
         "label": "knew", "confidence": 0.5},
    ],
}


@pytest.fixture(autouse=True)
def provider_env(monkeypatch):
    """Valid dummy provider config — the transport itself is a fixture."""
    for name in (*PROVIDER_ENV, "OCR_TIMEOUT", "VISION_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in PROVIDER_ENV.items():
        monkeypatch.setenv(name, value)


#: Account owning the processed run (R7: analysis reads are owner-scoped).
OWNER_EMAIL = "processing.owner@example.org"
OWNER_PASSWORD = "processing owner passphrase"


async def _process_and_seed(url: str) -> dict:
    """The full Module D processing chain, persisted through Module C."""
    from app.repositories.analysis_repository import attach_entities, create_analysis_run
    from app.repositories.user_repository import create_user
    from app.services.auth_service import hash_password
    from app.repositories.entity_repository import upsert_entities
    from app.repositories.record_repository import load_seed_records, seed_records
    from app.repositories.relationship_repository import (
        RelationshipInput,
        save_relationships,
    )
    from app.services.evidence_service import evaluate_relationship_evidence
    from app.services.retrieval_service import search_historical_records

    # Steps 1-2: D1 preparation + D2/D3 extraction (fixture transports).
    prepared = prepare_image(
        document_image(), filename="certificate.png", content_type="image/png"
    )
    extraction = await run_extraction(
        prepared, ocr_client=_ocr(OCR_TEXT), vision_client=_vision(VISION_PAYLOAD)
    )
    assert [e.name for e in extraction.entities] == [
        "Ada Lovelace",
        "Analytical Engine",
        "Punch",
    ]

    engine = create_engine(url)
    sessions = create_session_factory(engine)
    try:
        async with session_scope(sessions) as session:
            # Step 3: real retrieval over the real curated seeds.
            await seed_records(session, load_seed_records())
            hits = []
            for query in ("Ada Lovelace", "Analytical Engine", "Punch"):
                hits.extend(await search_historical_records(session, query, limit=10))
            records = {hit.record.id: hit.record for hit in hits}

            # Step 4: D4 candidates with provenance from those results.
            candidates, warnings = generate_relationship_candidates(
                extraction.relationships,
                extraction.entities,
                records=list(records.values()),
            )

            # Step 5: persist Module D's outputs through Module C.
            owner = await create_user(
                session,
                email=OWNER_EMAIL,
                password_hash=hash_password(OWNER_PASSWORD),
            )
            await create_analysis_run(
                session,
                analysis_id=RUN_ID,
                original_filename="certificate.png",
                content_type="image/png",
                user_id=owner.id,
            )
            db_entities = await upsert_entities(
                session,
                [
                    {"name": e.name, "entity_type": e.type, "confidence": e.confidence}
                    for e in extraction.entities
                ],
            )
            assert len(db_entities) == len(extraction.entities)
            # D3 ids (ent-N) map onto Module C's canonical row ids — the
            # mapping step B3 performs between extraction and persistence.
            d_to_db = {d.id: db.id for d, db in zip(extraction.entities, db_entities)}
            await attach_entities(
                session,
                RUN_ID,
                [
                    {"entity_id": db.id, "confidence": d.confidence}
                    for d, db in zip(extraction.entities, db_entities)
                ],
            )
            rows = await save_relationships(
                session,
                RUN_ID,
                [
                    RelationshipInput(
                        source_entity_id=d_to_db[c.source],
                        target_entity_id=d_to_db[c.target],
                        label=c.label,
                        verification_status=c.verification_status,  # "unverified"
                        # No relationship_id: C1's primary key is global, so
                        # Module C assigns the persisted id (as B3 does);
                        # the candidate id travels in the notes instead.
                        notes=[
                            "candidate: " + c.id,
                            f"sources: {', '.join(c.sources)}",
                            "records: " + (", ".join(c.record_ids) or "none"),
                        ],
                    )
                    for c in candidates
                ],
            )

            # Step 6: real C4 evaluation (overwrites status, persists
            # evidence) — keyed by the persisted row ids; save_relationships
            # preserves input order, so rows align with candidates.
            evaluations = [
                await evaluate_relationship_evidence(session, row.id) for row in rows
            ]
    finally:
        await engine.dispose()

    # Step 7: the graph response B3 returns/uses. Two id spaces after
    # persistence: candidate ids (rel-N, in-run) and Module C's row ids
    # (what the API serves) — statuses are mapped for both.
    statuses = {ev.relationship_id: ev.status for ev in evaluations}
    candidate_statuses = {
        candidate.id: evaluation.status
        for candidate, evaluation in zip(candidates, evaluations)
    }
    graph = build_graph(RUN_ID, extraction.entities, candidates, statuses=candidate_statuses)
    return {
        "extraction": extraction,
        "candidates": candidates,
        "warnings": warnings,
        "evaluations": evaluations,
        "statuses": candidate_statuses,
        "row_statuses": statuses,
        "graph": graph,
        "record_ids": sorted(records),
        "d_to_db": d_to_db,
    }


@pytest.fixture
def processed(monkeypatch, tmp_path) -> dict:
    """Migrated DB + the whole chain executed; app caches point at it."""
    url = f"sqlite:///{tmp_path / 'd5.db'}"
    command.upgrade(alembic_config(url), "head")
    outcome = asyncio.run(_process_and_seed(url))

    monkeypatch.setenv("DATABASE_URL", url)
    reset_settings_cache()
    reset_default_engine()
    yield outcome
    reset_settings_cache()
    reset_default_engine()


@pytest.fixture
def processed_client(processed):
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(Settings.from_env()))
    logged_in = client.post(
        "/api/auth/login",
        json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
    )
    assert logged_in.status_code == 200, logged_in.text
    return client


# ---------------------------------------------------------------------------
# Structured outputs validate against the frozen contract
# ---------------------------------------------------------------------------


def test_processing_chain_outputs_are_schema_valid(processed):
    extraction = processed["extraction"]
    for entity_obj in extraction.entities:  # B2 entity contract
        AnalysisEntity.model_validate(entity_obj.model_dump())

    graph = processed["graph"]  # B2 graph contract, round-tripped
    validated = GraphResponse.model_validate(graph.model_dump(by_alias=True))
    assert validated == graph

    node_ids = {node.id for node in graph.nodes}
    for edge in graph.edges:
        assert edge.source in node_ids and edge.target in node_ids
        assert edge.id in processed["statuses"]
        assert edge.verification_status == processed["statuses"][edge.id]

    # C4's real verdicts only — vocabulary respected, nothing invented.
    assert set(processed["statuses"].values()) <= set(VERIFICATION_STATUSES)
    assert processed["statuses"]["rel-1"] == "verified"  # seeds co-document it
    # Unknown-entity proposal was excluded, and reported, not silent.
    assert {c.id for c in processed["candidates"]} == {"rel-1", "rel-2"}
    assert any("Ghost Person" in w for w in processed["warnings"])
    # Provenance cites the actual retrieved records.
    rel_one = next(c for c in processed["candidates"] if c.id == "rel-1")
    assert rel_one.sources == ("vision", "record")
    assert rel_one.record_ids


def test_extraction_relationship_proposals_reached_d4(processed):
    """D3 carries the model's proposals through to D4 unchanged."""
    proposals = processed["extraction"].relationships
    assert [(p.source, p.target) for p in proposals] == [
        ("Ada Lovelace", "Analytical Engine"),
        ("Ada Lovelace", "Punch"),
        ("Ghost Person", "Ada Lovelace"),
    ]


# ---------------------------------------------------------------------------
# Read back over HTTP through B4's routes (the API contract)
# ---------------------------------------------------------------------------


def test_api_serves_the_graph_built_from_processing(processed, processed_client):
    response = processed_client.get(f"/api/analysis/{RUN_ID}/graph")

    assert response.status_code == 200
    body = response.json()
    assert body["analysisId"] == RUN_ID
    assert len(body["nodes"]) == 3 and len(body["edges"]) == 2

    node_ids = {node["id"] for node in body["nodes"]}
    for edge in body["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
        # Module C's persisted ids and C4's verdicts survived the round trip.
        assert edge["verificationStatus"] == processed["row_statuses"][edge["id"]]
    assert len(body["edges"]) == 2
    assert len({edge["id"] for edge in body["edges"]}) == 2  # globally unique ids


def test_api_serves_evidence_via_the_stable_relationship_id(processed, processed_client):
    graph = processed_client.get(f"/api/analysis/{RUN_ID}/graph").json()
    relationship_id = graph["edges"][0]["id"]  # the persisted row id
    response = processed_client.get(
        f"/api/analysis/{RUN_ID}/relationships/{relationship_id}/evidence"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["relationshipId"] == relationship_id
    assert body["verificationStatus"] == processed["row_statuses"][relationship_id]
    assert body["summary"]
    assert body["evidence"], "the real C4 evaluation persisted evidence rows"


def test_api_404s_for_an_unprocessed_analysis(processed, processed_client):
    """Error vs result stay distinct for an id the chain never produced."""
    response = processed_client.get("/api/analysis/run-d5-missing/graph")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"