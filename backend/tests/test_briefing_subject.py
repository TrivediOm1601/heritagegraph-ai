"""Briefing-subject selection: the explanation must be about the subject.

Regression guard for a defect found by the multi-image evaluation: the
old rule (\"prefer any place\") briefed a Gutenberg Bible photograph about
the city of ``Mainz`` and a Nataraja bronze about ``Tamil Nadu``. The
subject is now chosen by retrieval evidence, so these tests pin:

* a candidate the record store actually knows wins over a broad
  container the store knows nothing about;
* when *nothing* retrieves, the old place-preference fallback is kept
  (the briefing then honestly reports having no relevant source).
"""

from __future__ import annotations

import pytest

from app.services.analysis_service import _briefing_query, _briefing_subject
from app.schemas.entity import AnalysisEntity

pytestmark = pytest.mark.anyio


_SEQUENCE = iter(range(1, 100))


def _entity(name: str, entity_type: str, confidence: float) -> AnalysisEntity:
    return AnalysisEntity(
        id=f"ent-{next(_SEQUENCE)}", name=name, type=entity_type, confidence=confidence
    )


async def test_subject_is_the_entity_the_record_store_knows(open_db):
    from app.db.session import session_scope
    from app.repositories.record_repository import load_seed_records, seed_records
    from app.services.retrieval_service import search_historical_records

    async with open_db() as sessions:
        async with session_scope(sessions) as session:
            await seed_records(session, load_seed_records())
            # Sanity: the seeded store really does know this subject.
            hits = await search_historical_records(session, "Ada Lovelace", limit=5)
            assert hits, "seed fixture changed — this test needs the Ada Lovelace record"

            # The old "prefer any place" rule picked London here even
            # though the model is far more confident the image shows the
            # person; the subject must follow confidence among candidates
            # the store can actually back with records.
            query, retrieved = await _briefing_subject(
                session,
                [
                    _entity("London", "place", 0.7),
                    _entity("Ada Lovelace", "person", 0.9),
                ],
            )
            assert query == "Ada Lovelace"
            assert retrieved


async def test_subject_falls_back_to_the_place_heuristic_without_hits(open_db):
    from app.db.session import session_scope
    from app.repositories.record_repository import load_seed_records, seed_records

    async with open_db() as sessions:
        async with session_scope(sessions) as session:
            await seed_records(session, load_seed_records())
            query, retrieved = await _briefing_subject(
                session,
                [
                    _entity("Untermenschenstein Castle", "place", 0.6),
                    _entity("Viborg", "place", 0.9),
                ],
            )
            # Nothing retrieves for either invented name → heuristic place pick.
            assert query == "Viborg"
            assert retrieved == []


def test_legacy_heuristic_prefers_a_place():
    assert _briefing_query(
        [_entity("Ada Lovelace", "person", 0.99), _entity("London", "place", 0.5)]
    ) == "London"
    assert _briefing_query([]) is None