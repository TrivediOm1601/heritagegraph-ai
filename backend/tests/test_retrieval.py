"""C2 tests — retrieval repositories (entities + historical records + runs).

These are **real database integration tests**: every test opens an async
engine over a SQLite file migrated by the actual Alembic pipeline, then
exercises the repositories exactly as Module B orchestration will. Nothing
about the storage layer is mocked; only the *engine target* is substituted
because this workspace has no PostgreSQL server (see INTEGRATION_ISSUES.md,
ASSUMPTION C2-1).

Layout note: modules under ``app.repositories`` are imported lazily inside
each test because importing a repository module pulls in ``app.db.models``
and therefore the *application* config — which must not be required to run
these tests.
"""

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio

from app.db.models import (  # noqa: E402
    AnalysisEntity,
    Entity,
    HistoricalRecord,
)
from app.db.session import (  # noqa: E402
    ConflictError,
    DatabaseUnavailableError,
    NotFoundError,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _seed_entity(sessions, name: str, entity_type: str) -> str:
    from app.repositories.entity_repository import upsert_entities

    async with sessions() as session:
        rows = await upsert_entities(
            session, [{"name": name, "entity_type": entity_type}]
        )
        await session.commit()
    return rows[0].id


async def _seed_records(sessions) -> None:
    async with sessions() as session:
        session.add_all(
            [
                HistoricalRecord(
                    id="rec-1",
                    title="Analytical Engine notes",
                    content="A description of the Analytical Engine and its printers.",
                    source="Science Museum",
                    year="1843",
                ),
                HistoricalRecord(
                    id="rec-2",
                    title="Ada correspondence",
                    content="Letters about Charles Babbage.",
                    source="British Library",
                    year="1851",
                ),
                HistoricalRecord(
                    id="rec-3",
                    title="Engineers of the age",
                    content="Profiles of Victorian engineers.",
                    source="National Archives",
                    year="c. 1880",
                ),
            ]
        )
        await session.commit()


# ---------------------------------------------------------------------------
# entity_repository
# ---------------------------------------------------------------------------


async def test_find_entities_matches_case_and_whitespace_insensitively(open_db):
    async with open_db() as sessions:
        from app.repositories.entity_repository import find_entities_by_normalized_names

        entity_id = await _seed_entity(sessions, "Ada Lovelace", "person")

        async with sessions() as session:
            rows = await find_entities_by_normalized_names(
                session, ["  ada   LOVELACE  ", "  ADA lovelace "]
            )
            assert [r.id for r in rows] == [entity_id]

            # Unknown names come back as no rows — not an error.
            assert await find_entities_by_normalized_names(session, ["Nobody"]) == []
            # Blank input never matches anything.
            assert await find_entities_by_normalized_names(session, ["   "]) == []


async def test_find_entities_filters_by_type(open_db):
    async with open_db() as sessions:
        from app.repositories.entity_repository import find_entities_by_normalized_names

        person_id = await _seed_entity(sessions, "Ada Lovelace", "person")
        await _seed_entity(sessions, "Ada Lovelace", "place")

        async with sessions() as session:
            only_people = await find_entities_by_normalized_names(
                session, ["ada lovelace"], entity_type="person"
            )
            assert [r.id for r in only_people] == [person_id]

            anything = await find_entities_by_normalized_names(
                session, ["ada lovelace"]
            )
            assert len(anything) == 2


async def test_find_entities_with_empty_input_skips_querying(open_db):
    async with open_db() as sessions:
        from app.repositories.entity_repository import find_entities_by_normalized_names

        await _seed_entity(sessions, "Ada Lovelace", "person")

        async with sessions() as session:
            assert await find_entities_by_normalized_names(session, []) == []


async def test_upsert_entities_deduplicates_within_call_and_across_calls(open_db):
    async with open_db() as sessions:
        from app.repositories.entity_repository import upsert_entities

        async with sessions() as session:
            first = await upsert_entities(
                session,
                [
                    {"name": "Ada Lovelace", "entity_type": "person"},
                    {"name": "  ada   lovelace ", "entity_type": "person"},
                    {"name": "Ada Lovelace", "entity_type": "place"},
                ],
            )
            await session.commit()

        # Same normalised (name, type) key twice -> one row; different type
        # -> another row.
        assert len(first) == 2
        assert {r.entity_type for r in first} == {"person", "place"}

        async with sessions() as session:
            second = await upsert_entities(
                session, [{"name": "ADA Lovelace", "entity_type": "person"}]
            )
            await session.commit()
            assert second[0].id == first[0].id  # reused, not duplicated

        async with sessions() as session:
            total = (await session.execute(select(Entity))).scalars().all()
            assert len(total) == 2


async def test_upsert_entities_rejects_unknown_types_with_a_sanitized_error(open_db):
    async with open_db() as sessions:
        from app.repositories.entity_repository import upsert_entities

        async with sessions() as session:
            with pytest.raises(ConflictError) as excinfo:
                await upsert_entities(
                    session, [{"name": "x", "entity_type": "dragon"}]
                )

        message = str(excinfo.value)
        assert "dragon" in message and "person" in message  # offender + vocabulary
        for leak in ("INSERT", "IntegrityError", "CHECK", "sqlite"):
            assert leak not in message


# ---------------------------------------------------------------------------
# record_repository
# ---------------------------------------------------------------------------


async def test_search_records_matches_title_content_and_source(open_db):
    async with open_db() as sessions:
        from app.repositories.record_repository import search_records

        await _seed_records(sessions)

        async with sessions() as session:
            by_title = await search_records(session, "ANALYTICAL")
            assert [r.id for r in by_title] == ["rec-1"]

            by_content = await search_records(session, "victorian")
            assert [r.id for r in by_content] == ["rec-3"]

            by_source = await search_records(session, "british library")
            assert [r.id for r in by_source] == ["rec-2"]


async def test_search_records_treats_like_wildcards_as_literals(open_db):
    async with open_db() as sessions:
        from app.repositories.record_repository import search_records

        async with sessions() as session:
            session.add(
                HistoricalRecord(
                    id="rec-pct",
                    title="Progress 100% complete",
                    source="Archive A",
                )
            )
            session.add(
                HistoricalRecord(
                    id="rec-cotton",
                    title="cotton transport guide",
                    source="Archive B",
                )
            )
            await session.commit()

            # "%" would match everything if it leaked into LIKE unescaped.
            pct = await search_records(session, "100%")
            assert [r.id for r in pct] == ["rec-pct"]

            # "_" would match any single character if it leaked unescaped.
            wildcard = await search_records(session, "c_tton")
            assert wildcard == []


async def test_search_records_blank_query_and_limits(open_db):
    async with open_db() as sessions:
        from app.repositories.record_repository import search_records

        await _seed_records(sessions)

        async with sessions() as session:
            assert await search_records(session, "   ") == []
            assert await search_records(session, "") == []

            # limit=0 is clamped to at least 1; a negative offset is clamped
            # to 0.
            clamped = await search_records(session, "e", limit=0, offset=-5)
            assert len(clamped) == 1

            page = await search_records(session, "e", limit=2, offset=1)
            assert len(page) == 2


async def test_search_records_ranks_title_matches_before_source_matches(open_db):
    async with open_db() as sessions:
        from app.repositories.record_repository import search_records

        async with sessions() as session:
            session.add_all(
                [
                    HistoricalRecord(
                        id="rec-in-title",
                        title="Babbage engine",
                        source="Archive Z",
                    ),
                    HistoricalRecord(
                        id="rec-in-source",
                        title="Notes",
                        source="Babbage foundation",
                    ),
                ]
            )
            await session.commit()

            results = await search_records(session, "babbage")
            assert [r.id for r in results] == ["rec-in-title", "rec-in-source"]


# ---------------------------------------------------------------------------
# analysis_repository
# ---------------------------------------------------------------------------


async def test_create_get_and_update_analysis_run(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import (
            create_analysis_run,
            get_analysis_run,
            update_analysis_run,
        )

        async with sessions() as session:
            run = await create_analysis_run(
                session,
                analysis_id="run-42",
                original_filename="plaque.jpg",
                content_type="image/jpeg",
            )
            await session.commit()
        assert run.status == "queued" and run.created_at is not None

        async with sessions() as session:
            fetched = await get_analysis_run(session, "run-42")
            assert fetched.id == "run-42"
            assert fetched.original_filename == "plaque.jpg"

            await update_analysis_run(
                session, "run-42", status="processing", progress=0.5, error="x"
            )
            await session.commit()

            again = await get_analysis_run(session, "run-42")
            assert again.status == "processing" and again.progress == 0.5

            # error=None means "clear the failure"; an omitted field (here:
            # progress) is untouched.
            await update_analysis_run(session, "run-42", error=None)
            await session.commit()
            cleared = await get_analysis_run(session, "run-42")
            assert cleared.error is None and cleared.progress == 0.5

            assert await get_analysis_run(session, "missing") is None


async def test_update_unknown_analysis_run_is_not_found(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import update_analysis_run

        async with sessions() as session:
            with pytest.raises(NotFoundError) as excinfo:
                await update_analysis_run(session, "ghost", status="failed")

        message = str(excinfo.value)
        assert "ghost" in message
        for leak in ("UPDATE", "analysis_runs", "no such", "sqlite"):
            assert leak not in message


async def test_create_analysis_run_rejects_unknown_status(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import create_analysis_run

        async with sessions() as session:
            with pytest.raises(ConflictError) as excinfo:
                await create_analysis_run(
                    session, analysis_id="run-bad", status="exploded"
                )

        message = str(excinfo.value)
        assert "exploded" in message and "queued" in message
        assert "CHECK" not in message and "INSERT" not in message


async def test_attach_entities_links_each_pair_once_and_updates_confidence(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import (
            attach_entities,
            create_analysis_run,
        )

        async with sessions() as session:
            await create_analysis_run(session, analysis_id="run-1")
            await session.commit()
        entity_id = await _seed_entity(sessions, "Ada Lovelace", "person")

        async with sessions() as session:
            link = {"entity_id": entity_id, "confidence": 0.5}
            await attach_entities(session, "run-1", [link])
            await session.commit()
            link = {"entity_id": entity_id, "confidence": 0.9}
            await attach_entities(session, "run-1", [link])
            await session.commit()

            pairs = (
                await session.execute(
                    select(func.count()).select_from(AnalysisEntity)
                )
            ).scalar_one()
            assert pairs == 1  # relinked, not duplicated

            row = (await session.execute(select(AnalysisEntity))).scalars().first()
            assert row.confidence == pytest.approx(0.9)


async def test_attach_entities_with_unknown_entity_is_not_found(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import (
            attach_entities,
            create_analysis_run,
        )

        async with sessions() as session:
            await create_analysis_run(session, analysis_id="run-1")
            await session.commit()

        async with sessions() as session:
            with pytest.raises(NotFoundError) as excinfo:
                await attach_entities(
                    session, "run-1", [{"entity_id": "no-such-entity"}]
                )

        message = str(excinfo.value)
        assert "no-such-entity" in message
        for leak in ("SELECT", "sqlite", "IntegrityError"):
            assert leak not in message


# ---------------------------------------------------------------------------
# database disconnection — sanitized, never raw
# ---------------------------------------------------------------------------


async def test_database_failure_is_reported_as_a_sanitized_repository_error(tmp_path):
    """A database that cannot be opened produces DatabaseUnavailableError,
    never the driver's raw message (paths, driver internals, SQL)."""
    from app.db.session import create_engine, create_session_factory
    from app.repositories.record_repository import search_records

    # The parent directory does not exist, so SQLite cannot open the file.
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/no-such-dir/x.db")
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            with pytest.raises(DatabaseUnavailableError) as excinfo:
                await search_records(session, "anything")
    finally:
        await engine.dispose()

    message = str(excinfo.value)
    assert "unable to open" not in message.lower()
    for leak in ("sqlite", "OperationalError", str(tmp_path), "SELECT"):
        assert leak not in message


# ---------------------------------------------------------------------------
# C3 — curated dataset and retrieval service
# ---------------------------------------------------------------------------


def test_seed_dataset_is_valid_and_traceable():
    """Every curated record cites a title, publisher, URL, text and provenance."""
    from app.repositories.record_repository import load_seed_records

    records = load_seed_records()
    assert len(records) >= 12  # a small but real dataset

    ids = [record["id"] for record in records]
    assert len(set(ids)) == len(ids)

    for record in records:
        assert record["title"] and record["year"]
        assert record["source"]  # publisher — the traceable source
        assert record["url"].lower().startswith("https://")
        assert record["content"]
        assert record["provenance"]


def test_seed_file_meta_flags_it_as_development_only():
    """The seed declares itself as NOT a substitute for a source of truth."""
    import json

    from app.repositories.record_repository import DEFAULT_SEED_PATH

    data = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    meta = data["_meta"]
    assert "not a substitute for a verified source of truth" in meta["purpose"]
    assert meta["record_count"] == len(data["records"])
    for record in data["records"]:
        assert record["provenance"].strip()
        assert record["url"].lower().startswith("https://")


def test_malformed_seed_file_is_rejected_with_a_friendly_error(tmp_path):
    from app.repositories.record_repository import load_seed_records

    bad = tmp_path / "seed.json"
    bad.write_text('{"records": [{"id": "x"}]}', encoding="utf-8")

    with pytest.raises(ConflictError) as excinfo:
        load_seed_records(bad)
    message = str(excinfo.value)
    assert "title" in message  # names the missing fields
    assert "Traceback" not in message


async def _seed_curated_records(sessions) -> int:
    from app.repositories.record_repository import load_seed_records, seed_records

    async with sessions() as session:
        added = await seed_records(session, load_seed_records())
        await session.commit()
    return added


async def test_seed_records_load_into_database_idempotently(open_db):
    async with open_db() as sessions:
        from app.repositories.record_repository import load_seed_records, seed_records

        seed = load_seed_records()
        assert await _seed_curated_records(sessions) == len(seed)
        # Re-running the seed adds nothing and duplicates nothing.
        assert await _seed_curated_records(sessions) == 0

        async with sessions() as session:
            from sqlalchemy import func, select

            count = (await session.execute(
                select(func.count()).select_from(HistoricalRecord)
            )).scalar_one()
            assert count == len(seed)
            # Seeding again inside a fresh session is still a no-op.
            assert await seed_records(session, seed) == 0


async def test_seed_backfills_a_missing_source_url(open_db):
    """A row stored before ``url`` existed must regain its source link.

    The seed is add-only, so a database created before revision 0002 kept
    ``url = NULL`` forever and every "view source" link was dead. The
    backfill repairs the link and leaves the curated text alone.
    """
    async with open_db() as sessions:
        async with sessions() as session:
            session.add(
                HistoricalRecord(
                    id="rec-ada-lovelace",
                    title="Ada Lovelace",
                    source="Encyclopædia Britannica",
                    content="A pre-existing curated summary.",
                )
            )
            await session.commit()

        from app.repositories.record_repository import load_seed_records, seed_records

        seed = load_seed_records()
        async with sessions() as session:
            added = await seed_records(session, seed)
            await session.commit()
        # Every seed row is inserted except the one that already existed.
        assert added == len(seed) - 1

        async with sessions() as session:
            row = await session.get(HistoricalRecord, "rec-ada-lovelace")
            assert row is not None
            assert (row.url or "").startswith("https://")  # link restored
            assert row.content == "A pre-existing curated summary."  # text untouched


async def test_search_finds_relevant_record_with_contract_shape(open_db):
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            results = await search_historical_records(session, "Ada Lovelace")

        assert results, "a relevant query must return results"
        top = results[0]
        assert top.record.id == "rec-ada-lovelace"  # exact title match ranks first
        assert 0 < top.score <= 1

        match = top.to_match()
        assert set(match) == {
            "id",
            "title",
            "year",
            "score",
            "source",
            "url",
            "excerpt",
            "relevance",
        }
        assert match["relevance"] == "high"
        assert match["url"].lower().startswith("https://")  # R3: seed URL persists
        assert match["id"] == "rec-ada-lovelace"
        assert match["source"].strip()  # every result keeps its traceable source
        # An exact title hit sits on the top tier of the scoring ladder.
        assert top.score >= 0.9


async def test_search_normalizes_case_and_whitespace(open_db):
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import normalize_query, search_historical_records

        async with sessions() as session:
            results = await search_historical_records(session, "  ADA   LOVELACE ")

        assert results and results[0].record.id == "rec-ada-lovelace"
        assert normalize_query("  ADA   LOVELACE ") == "ada lovelace"


async def test_unrelated_query_returns_no_results(open_db):
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            results = await search_historical_records(
                session, "quantum submarine expedition"
            )

        assert results == []  # no results, not an error


async def test_alternate_spelling_finds_the_record(open_db):
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            # Dataset spells it "colour" (photography record).
            us = await search_historical_records(session, "color")
            # Dataset spells it "program" (Grace Hopper record).
            gb = await search_historical_records(session, "programme")

        assert "rec-photography" in [hit.record.id for hit in us]
        assert all(hit.score > 0 for hit in us)
        assert "rec-grace-hopper" in [hit.record.id for hit in gb]


async def test_short_terms_are_not_probed_on_their_own(open_db):
    """Regression (R2 end-to-end): a 2-character token like "ki" matches
    inside unrelated words ("talking"), so it must not be probed or
    scored individually — an unrelated record must never surface just
    because the database is non-empty."""
    async with open_db() as sessions:
        async with sessions() as session:
            session.add(
                HistoricalRecord(
                    id="rec-talking-bird",
                    title="Talking bird exhibit",
                    source="Museum Notes",
                    content="The talking bird exhibit opened in 1900.",
                )
            )
            await session.commit()

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            # "ki" appears inside "talking" — no genuine match exists.
            assert await search_historical_records(session, "rani ki vav") == []
            # …but an explicit short query still searches for its own text.
            hits = await search_historical_records(session, "ki")
            assert [hit.record.id for hit in hits] == ["rec-talking-bird"]


async def test_empty_query_returns_no_results(open_db):
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            assert await search_historical_records(session, "") == []
            assert await search_historical_records(session, "   ") == []


async def test_search_returns_an_excerpt_around_the_match(open_db):
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            results = await search_historical_records(session, "Enigma")

        assert [hit.record.id for hit in results] == ["rec-alan-turing"]
        top = results[0]
        assert top.excerpt
        assert "Enigma" in top.excerpt
        assert len(top.excerpt) < len(top.record.content)  # a window, not the whole text
        assert "…" in top.excerpt  # trimmed around the match


async def test_multi_term_query_matches_non_adjacent_terms(open_db):
    """'babbage mathematician' must find records mentioning both words
    even though they are never adjacent in the text."""
    async with open_db() as sessions:
        await _seed_curated_records(sessions)

        from app.services.retrieval_service import search_historical_records

        async with sessions() as session:
            results = await search_historical_records(
                session, "babbage mathematician"
            )

        ids = [hit.record.id for hit in results]
        assert "rec-charles-babbage" in ids
        # The record matching *all* terms must outrank records that match only
        # one of them (the old flat ladder could not express this).
        babbage = next(hit for hit in results if hit.record.id == "rec-charles-babbage")
        assert all(hit.score <= babbage.score for hit in results)
        assert babbage.score > 0.5
