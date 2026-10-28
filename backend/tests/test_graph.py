"""D4 tests — candidate relationships and graph construction.

Required cases: valid relationship, unsupported candidate, missing
entity, empty graph, duplicate edge, malformed relationship — plus the
two acceptance criteria made executable: **no fabricated edges**
(records alone never create one) and **every edge reference points to a
valid node**.

The final test is the completion evidence: a sample graph built from
*actual Module C retrieval results* (real Alembic-migrated database,
real seed records, real ``search_historical_records``).
"""

from __future__ import annotations

import pytest

from app.db.models import HistoricalRecord
from app.integrations.vision_model import VisionRelationship
from app.schemas.entity import AnalysisEntity
from app.schemas.graph import GraphResponse
from app.services.graph_service import build_graph
from app.services.relationship_service import (
    MAX_LABEL_LENGTH,
    RelationshipCandidate,
    clean_label,
    generate_relationship_candidates,
)


# ---------------------------------------------------------------------------
# Helpers (plain contract objects — no database needed for unit cases)
# ---------------------------------------------------------------------------


def ent(id_: str, name: str, type_: str = "person") -> AnalysisEntity:
    return AnalysisEntity(id=id_, name=name, type=type_)


def prop(source: str, target: str, label: str | None = None, confidence: float | None = None):
    return VisionRelationship(source=source, target=target, label=label, confidence=confidence)


def rec(id_: str, title: str, content: str) -> HistoricalRecord:
    return HistoricalRecord(id=id_, title=title, source="Test Archive", content=content)


BASE_ENTITIES = [ent("ent-1", "Ada Lovelace"), ent("ent-2", "Analytical Engine", "event")]


def build(entities, proposals, *, records=(), statuses=None):
    candidates, warnings = generate_relationship_candidates(
        proposals, entities, records=records
    )
    graph = build_graph("run-1", entities, candidates, statuses=statuses)
    return candidates, warnings, graph


# ---------------------------------------------------------------------------
# Valid relationship
# ---------------------------------------------------------------------------


def test_valid_relationship_becomes_an_edge_with_valid_references():
    candidates, warnings, graph = build(
        BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine", "wrote notes for", 0.9)]
    )

    assert warnings == []
    (candidate,) = candidates
    assert candidate.id == "rel-1"
    assert (candidate.source, candidate.target) == ("ent-1", "ent-2")
    assert candidate.verification_status == "unverified"  # never verified here

    (edge,) = graph.edges
    node_ids = {node.id for node in graph.nodes}
    assert edge.source in node_ids and edge.target in node_ids
    assert edge.label == "wrote notes for"
    assert edge.verification_status == "unverified"
    # Full schema round-trip against the frozen contract.
    assert GraphResponse.model_validate(graph.model_dump(by_alias=True)) == graph


def test_endpoints_resolve_case_and_whitespace_insensitively():
    candidates, warnings, _ = build(
        BASE_ENTITIES, [prop("  ada   LOVELACE ", "ANALYTICAL ENGINE")]
    )
    assert warnings == []
    assert len(candidates) == 1
    assert (candidates[0].source, candidates[0].target) == ("ent-1", "ent-2")


def test_ids_are_stable_across_runs():
    proposals = [
        prop("Ada Lovelace", "Analytical Engine", "a"),
        prop("Analytical Engine", "Ada Lovelace", "b"),  # reverse is a distinct ordered pair
    ]
    first = generate_relationship_candidates(proposals, BASE_ENTITIES)[0]
    second = generate_relationship_candidates(proposals, BASE_ENTITIES)[0]
    assert [c.id for c in first] == [c.id for c in second] == ["rel-1", "rel-2"]


# ---------------------------------------------------------------------------
# Unsupported candidate
# ---------------------------------------------------------------------------


def test_unsupported_candidate_is_labeled_from_evaluation_not_reinvented():
    _, _, graph = build(
        BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine", "invented by")],
        statuses={"rel-1": "unsupported"},
    )
    (edge,) = graph.edges
    assert edge.verification_status == "unsupported"  # C4's verdict, passed through


def test_unknown_status_values_fall_back_to_unverified():
    _, _, graph = build(
        BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine")],
        statuses={"rel-1": "definitely-proven"},
    )
    (edge,) = graph.edges
    assert edge.verification_status == "unverified"


def test_no_evaluation_means_unverified_never_verified():
    _, _, graph = build(BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine")])
    assert graph.edges[0].verification_status == "unverified"


# ---------------------------------------------------------------------------
# Missing entity / ambiguous / self-loop
# ---------------------------------------------------------------------------


def test_missing_entity_excludes_the_candidate_with_a_warning():
    candidates, warnings, graph = build(
        BASE_ENTITIES, [prop("Ada Lovelace", "Nobody Real", "knew")]
    )
    assert candidates == []
    assert graph.edges == []
    assert any('unknown entities' in w and '"Nobody Real"' in w for w in warnings)


def test_ambiguous_endpoint_is_excluded_not_arbitrarily_bound():
    entities = [*BASE_ENTITIES, ent("ent-3", "Chelsea", "place"), ent("ent-4", "Chelsea", "event")]
    candidates, warnings, graph = build(entities, [prop("Chelsea", "Ada Lovelace")])
    assert candidates == []
    assert graph.edges == []
    assert any("matches several entities" in w for w in warnings)


def test_self_loop_is_excluded():
    entities = [ent("ent-1", "Ada Lovelace")]
    candidates, warnings, graph = build(entities, [prop("Ada Lovelace", "ada lovelace")])
    assert candidates == []
    assert graph.edges == []
    assert any("Self-referencing" in w for w in warnings)


# ---------------------------------------------------------------------------
# Empty graph
# ---------------------------------------------------------------------------


def test_empty_inputs_produce_a_valid_empty_graph():
    candidates, warnings, graph = build([], [])
    assert candidates == [] and warnings == []
    assert graph.nodes == [] and graph.edges == []
    assert graph.analysis_id == "run-1"
    GraphResponse.model_validate(graph.model_dump(by_alias=True))


def test_entities_without_proposals_produce_no_edges():
    """Acceptance: records/entities alone never fabricate an edge."""
    records = [rec("rec-1", "Ada and the Engine", "Ada Lovelace worked on the Analytical Engine.")]
    _, warnings, graph = build(BASE_ENTITIES, [], records=records)
    assert graph.edges == []  # co-occurrence is not a relationship
    assert warnings == []
    assert len(graph.nodes) == 2


# ---------------------------------------------------------------------------
# Duplicate edge
# ---------------------------------------------------------------------------


def test_duplicate_proposals_collapse_into_one_edge():
    candidates, warnings, graph = build(
        BASE_ENTITIES,
        [
            prop("Ada Lovelace", "Analytical Engine", "wrote notes for", 0.7),
            prop("Ada Lovelace", "Analytical Engine", "annotated", 0.9),
        ],
    )
    assert len(candidates) == 1
    (candidate,) = candidates
    assert candidate.id == "rel-1"
    assert candidate.label == "wrote notes for"  # first label kept
    assert candidate.confidence == 0.9  # strongest confidence kept
    assert len(graph.edges) == 1


def test_graph_drops_hand_made_duplicate_ids_and_pairs():
    entities = BASE_ENTITIES
    def make(id_, source, target):
        return RelationshipCandidate(
            id=id_, source=source, target=target,
            source_name="a", target_name="b", label="x", confidence=None,
            verification_status="unverified", sources=("vision",), record_ids=(),
        )

    graph = build_graph(
        "run-1",
        entities,
        [
            make("rel-1", "ent-1", "ent-2"),
            make("rel-1", "ent-1", "ent-2"),   # duplicate id
            make("rel-2", "ent-1", "ent-2"),   # duplicate pair
            make("rel-3", "ent-2", "ent-1"),   # reverse pair: distinct, kept
            make("rel-4", "ent-1", "ghost"),   # dangling reference
        ],
    )
    assert [edge.id for edge in graph.edges] == ["rel-1", "rel-3"]


# ---------------------------------------------------------------------------
# Malformed relationship
# ---------------------------------------------------------------------------


def test_blank_endpoints_are_excluded():
    candidates, warnings, _ = build(BASE_ENTITIES, [prop("   ", "Analytical Engine")])
    assert candidates == []
    assert any("empty endpoint" in w for w in warnings)


def test_label_is_cleaned_and_length_validated():
    assert clean_label(None) == (None, False)
    assert clean_label("   ") == (None, False)
    assert clean_label("wrote\nnotes\tfor") == ("wrote notes for", False)
    long = "x" * (MAX_LABEL_LENGTH + 40)
    value, truncated = clean_label(long)
    assert truncated is True and len(value) == MAX_LABEL_LENGTH

    # D2's schema would reject an over-long label before D4 ever sees it;
    # model_construct simulates a direct caller so D4's own guard (the
    # DB's String(512) is the hard limit) is exercised too.
    oversized = VisionRelationship.model_construct(
        source="Ada Lovelace", target="Analytical Engine", label=long, confidence=None
    )
    candidates, warnings, graph = build(BASE_ENTITIES, [oversized])
    (candidate,) = candidates
    assert len(candidate.label) == MAX_LABEL_LENGTH
    assert any("longer than" in w for w in warnings)

    # A blank label yields a label-less edge — valid, not fabricated text.
    (no_label,) = generate_relationship_candidates(
        [prop("Ada Lovelace", "Analytical Engine", "  ")], BASE_ENTITIES
    )[0]
    assert no_label.label is None


def test_empty_proposal_list_is_not_an_error():
    candidates, warnings = generate_relationship_candidates([], BASE_ENTITIES)
    assert candidates == [] and warnings == []


# ---------------------------------------------------------------------------
# Provenance (records corroborate, never create)
# ---------------------------------------------------------------------------


def test_co_mentioned_endpoints_gain_record_provenance():
    records = [
        rec("rec-1", "Engine notes", "Ada Lovelace wrote notes on the Analytical Engine."),
        rec("rec-2", "Unrelated", "The Analytical Engine was proposed by Babbage."),  # one endpoint only
    ]
    candidates, _, _ = build(BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine")], records=records)
    (candidate,) = candidates
    assert candidate.sources == ("vision", "record")
    assert candidate.record_ids == ("rec-1",)  # only the co-mentioning record


def test_unco_mentioned_proposal_keeps_vision_only_provenance():
    records = [rec("rec-2", "Unrelated", "The Analytical Engine was proposed by Babbage.")]
    candidates, _, _ = build(BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine")], records=records)
    (candidate,) = candidates
    assert candidate.sources == ("vision",)
    assert candidate.record_ids == ()


def test_word_boundaries_prevent_false_corroboration():
    records = [rec("rec-9", "Madagascar trade", "Madagascar traded with the Analytical Engine.") ]
    candidates, _, _ = build(BASE_ENTITIES, [prop("Ada Lovelace", "Analytical Engine")], records=records)
    assert candidates[0].record_ids == ()  # "Ada" must not match inside "Madagascar"


# ---------------------------------------------------------------------------
# Completion evidence — sample graph from ACTUAL retrieval results
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sample_graph_from_actual_retrieval_results(migrated_db, open_db):
    """Real migrated DB + real seed records + real retrieval → sample graph."""
    from app.repositories.record_repository import load_seed_records, seed_records
    from app.db.session import session_scope
    from app.services.retrieval_service import search_historical_records

    async with open_db() as sessions:
        async with session_scope(sessions) as session:
            await seed_records(session, load_seed_records())
            hits = []
            for query in ("Ada Lovelace", "Analytical Engine"):
                hits.extend(await search_historical_records(session, query, limit=10))
    records_by_id = {hit.record.id: hit.record for hit in hits}
    assert records_by_id, "the curated seed must be retrievable"

    entities = [
        ent("ent-1", "Ada Lovelace", "person"),
        ent("ent-2", "Analytical Engine", "event"),
    ]
    proposals = [prop("Ada Lovelace", "Analytical Engine", "wrote notes for", 0.92)]

    candidates, warnings, graph = build(entities, proposals, records=list(records_by_id.values()))

    assert warnings == []
    (candidate,) = candidates
    assert candidate.sources == ("vision", "record"), "seed records co-mention both endpoints"
    assert candidate.record_ids, "provenance must cite the actual retrieved records"

    sample = build_graph("run-sample", entities, candidates)
    validated = GraphResponse.model_validate(sample.model_dump(by_alias=True))
    assert len(validated.nodes) == 2 and len(validated.edges) == 1
    assert validated.edges[0].verification_status == "unverified"
    assert validated.edges[0].source in {node.id for node in validated.nodes}


# ---------------------------------------------------------------------------
# D5 — processing boundaries at the graph seam
# ---------------------------------------------------------------------------


def test_statuses_map_may_cover_only_some_candidates():
    """C4 evaluates per relationship: an id absent from the map stays unverified."""
    candidates, _, _ = build(
        BASE_ENTITIES,
        [
            prop("Ada Lovelace", "Analytical Engine", "wrote notes for"),
            prop("Analytical Engine", "Ada Lovelace", "influenced"),
        ],
    )
    graph = build_graph(
        "run-1", BASE_ENTITIES, candidates, statuses={"rel-1": "disputed"}
    )
    statuses = {edge.id: edge.verification_status for edge in graph.edges}
    assert statuses == {"rel-1": "disputed", "rel-2": "unverified"}


def test_label_at_exact_contract_limit_is_kept_untruncated():
    exact = "z" * MAX_LABEL_LENGTH
    candidates, warnings = generate_relationship_candidates(
        [prop("Ada Lovelace", "Analytical Engine", exact)], BASE_ENTITIES
    )
    assert warnings == []
    assert candidates[0].label == exact  # boundary inclusive: 512 kept whole


def test_empty_statuses_map_leaves_every_edge_unverified():
    _, _, graph = build(
        BASE_ENTITIES,
        [prop("Ada Lovelace", "Analytical Engine", "wrote notes for")],
        statuses={},
    )
    assert all(edge.verification_status == "unverified" for edge in graph.edges)