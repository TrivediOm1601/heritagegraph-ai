"""B3 regression tests — how one analysis assembles its match list.

Found during the R2 end-to-end run: ``_retrieve_records`` used to keep the
*first* hit per record id, so a record first discovered by an incidental
probe carried that probe's low score forever — an exact title match could
sit at 0.75 (because an earlier entity name matched its content first) and
fall out of the capped ``MAX_MATCHES`` list while weaker live hits stayed
in. The best-scoring hit now wins, regardless of probe order.
"""

from __future__ import annotations

import pytest

from app.services import analysis_service

pytestmark = pytest.mark.anyio


def _hit(record_id: str, title: str, score: float, url: str | None = None):
    """A RetrievedRecord-shaped stand-in built from a real model row."""
    from app.db.models import HistoricalRecord
    from app.services.retrieval_service import RetrievedRecord

    return RetrievedRecord(
        record=HistoricalRecord(
            id=record_id, title=title, source="Archive", url=url, content=title
        ),
        excerpt=None,
        score=score,
    )


async def test_retrieve_records_keeps_the_best_hit_per_record(monkeypatch):
    """Same record found by two probes → its highest score wins."""
    calls: list[str] = []

    async def fake_search(session, query, *, limit=20, offset=0):
        calls.append(query)
        if query == "1843":  # incidental probe: content match only
            return [_hit("rec-ada", "Ada Lovelace", 0.75)]
        # The entity's own name: exact title match.
        return [_hit("rec-ada", "Ada Lovelace", 0.9)]

    monkeypatch.setattr(analysis_service, "search_historical_records", fake_search)

    records, hits = await analysis_service._retrieve_records(
        None, ["1843", "Ada Lovelace"]
    )

    assert calls == ["1843", "Ada Lovelace"]
    assert set(records) == {"rec-ada"}  # one record, not duplicated
    assert hits["rec-ada"].score == 0.9  # best probe wins, order-independent


async def test_retrieve_records_is_order_independent(monkeypatch):
    """Reversing the probe order yields the same scores (was not true
    before the fix, where the first probe pinned the score)."""
    from app.services.retrieval_service import RetrievedRecord

    async def fake_search(session, query, *, limit=20, offset=0):
        score = 0.75 if query == "1843" else 0.9
        return [_hit("rec-ada", "Ada Lovelace", score)]

    monkeypatch.setattr(analysis_service, "search_historical_records", fake_search)

    _, forward = await analysis_service._retrieve_records(
        None, ["1843", "Ada Lovelace"]
    )
    _, reverse = await analysis_service._retrieve_records(
        None, ["Ada Lovelace", "1843"]
    )

    assert isinstance(reverse["rec-ada"], RetrievedRecord)
    assert forward["rec-ada"].score == reverse["rec-ada"].score == 0.9


async def test_retrieve_records_probes_are_capped(monkeypatch):
    """At most MAX_MATCH_QUERIES entity names are sent to retrieval."""
    calls: list[str] = []

    async def fake_search(session, query, *, limit=20, offset=0):
        calls.append(query)
        return []

    monkeypatch.setattr(analysis_service, "search_historical_records", fake_search)

    names = [f"entity-{index}" for index in range(10)]
    await analysis_service._retrieve_records(None, names)

    assert len(calls) == analysis_service.MAX_MATCH_QUERIES