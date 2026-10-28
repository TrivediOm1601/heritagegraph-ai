"""C2 tests — persistence repositories (relationships + evidence) and transactions.

Like ``test_retrieval.py`` these run against the real Alembic-migrated
schema through an async engine; repository modules are imported lazily so
the suite does not depend on application configuration being present.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

pytestmark = pytest.mark.anyio

from app.db.models import AnalysisRun, Evidence, HistoricalRecord, Relationship  # noqa: E402
from app.db.session import (  # noqa: E402
    ConflictError,
    DatabaseUnavailableError,
    NotFoundError,
    RepositoryError,
    session_scope,
    translate_db_error,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _seed_run_with_entities(
    sessions, run_id: str = "run-1"
) -> tuple[str, str]:
    """Create one analysis run with two entities; returns their ids."""
    from app.repositories.analysis_repository import attach_entities, create_analysis_run
    from app.repositories.entity_repository import upsert_entities

    async with sessions() as session:
        await create_analysis_run(session, analysis_id=run_id)
        rows = await upsert_entities(
            session,
            [
                {"name": "Ada Lovelace", "entity_type": "person"},
                {"name": "Analytical Engine", "entity_type": "event"},
            ],
        )
        await attach_entities(
            session,
            run_id,
            [{"entity_id": r.id, "confidence": 0.8} for r in rows],
        )
        await session.commit()
    return rows[0].id, rows[1].id


async def _save_relationships(sessions, items, run_id: str = "run-1"):
    from app.repositories.relationship_repository import save_relationships

    async with session_scope(sessions) as session:
        return await save_relationships(session, run_id, items)


async def _seed_relationship(sessions) -> str:
    source, target = await _seed_run_with_entities(sessions)
    saved = await _save_relationships(
        sessions, [{"source": source, "target": target, "label": "notes"}]
    )
    return saved[0].id


# ---------------------------------------------------------------------------
# relationship_repository
# ---------------------------------------------------------------------------


async def test_save_relationships_creates_edges_with_generated_ids(open_db):
    async with open_db() as sessions:
        source, target = await _seed_run_with_entities(sessions)

        saved = await _save_relationships(
            sessions,
            [
                {
                    "source": source,
                    "target": target,
                    "label": "wrote notes for",
                    "verificationStatus": "verified",
                }
            ],
        )

        assert len(saved) == 1
        rel = saved[0]
        assert rel.id  # model default generates the id
        assert rel.source_entity_id == source and rel.target_entity_id == target
        assert rel.verification_status == "verified"
        assert rel.label == "wrote notes for"

        # Persisted, not just returned.
        async with sessions() as session:
            rows = (await session.execute(select(Relationship))).scalars().all()
            assert [r.id for r in rows] == [rel.id]


async def test_save_relationships_upserts_duplicate_pairs(open_db):
    async with open_db() as sessions:
        source, target = await _seed_run_with_entities(sessions)

        first = await _save_relationships(
            sessions, [{"source": source, "target": target, "label": "old"}]
        )
        second = await _save_relationships(
            sessions,
            [
                {
                    "source": source,
                    "target": target,
                    "label": "new",
                    "verificationStatus": "disputed",
                }
            ],
        )

        assert first[0].id == second[0].id  # same relationship updated in place
        async with sessions() as session:
            rows = (await session.execute(select(Relationship))).scalars().all()
            assert len(rows) == 1
            assert rows[0].label == "new"
            assert rows[0].verification_status == "disputed"


async def test_save_relationships_preserves_supplied_ids(open_db):
    async with open_db() as sessions:
        source, target = await _seed_run_with_entities(sessions)

        # "id" is the contract key (GraphEdge.id).
        saved = await _save_relationships(
            sessions,
            [{"id": "rel-99", "source": source, "target": target}],
        )
        assert saved[0].id == "rel-99"


async def test_save_relationships_unknown_analysis_is_not_found(open_db):
    async with open_db() as sessions:
        source, target = await _seed_run_with_entities(sessions)

        with pytest.raises(NotFoundError) as excinfo:
            await _save_relationships(
                sessions,
                [{"source": source, "target": target}],
                run_id="ghost-run",
            )

        message = str(excinfo.value)
        assert "ghost-run" in message
        for leak in ("SELECT", "analysis_runs", "sqlite"):
            assert leak not in message


async def test_save_relationships_unknown_entity_is_not_found(open_db):
    async with open_db() as sessions:
        source, _ = await _seed_run_with_entities(sessions)

        with pytest.raises(NotFoundError) as excinfo:
            await _save_relationships(
                sessions, [{"source": source, "target": "ghost-entity"}]
            )

        message = str(excinfo.value)
        assert "ghost-entity" in message
        for leak in ("SELECT", "INSERT", "sqlite"):
            assert leak not in message


async def test_save_relationships_rejects_self_loops(open_db):
    async with open_db() as sessions:
        source, _ = await _seed_run_with_entities(sessions)

        with pytest.raises(ConflictError) as excinfo:
            await _save_relationships(sessions, [{"source": source, "target": source}])

        message = str(excinfo.value)
        assert "itself" in message
        assert "CHECK" not in message and "INSERT" not in message


async def test_save_relationships_rejects_unknown_verification_status(open_db):
    async with open_db() as sessions:
        source, target = await _seed_run_with_entities(sessions)

        with pytest.raises(ConflictError) as excinfo:
            await _save_relationships(
                sessions,
                [
                    {
                        "source": source,
                        "target": target,
                        "verificationStatus": "definitely-true",
                    }
                ],
            )

        message = str(excinfo.value)
        assert "definitely-true" in message and "unverified" in message
        for leak in ("CHECK", "INSERT", "sqlite"):
            assert leak not in message


async def test_relationships_default_to_unverified_never_verified(open_db):
    """The repository must never coerce an absent status to 'verified'."""
    async with open_db() as sessions:
        source, target = await _seed_run_with_entities(sessions)

        saved = await _save_relationships(
            sessions, [{"source": source, "target": target}]
        )
        assert saved[0].verification_status == "unverified"


# ---------------------------------------------------------------------------
# evidence_repository
# ---------------------------------------------------------------------------


async def test_save_evidence_creates_items_and_replaces_previous_set(open_db):
    async with open_db() as sessions:
        rel_id = await _seed_relationship(sessions)

        from app.repositories.evidence_repository import save_evidence

        async with session_scope(sessions) as session:
            saved = await save_evidence(
                session,
                rel_id,
                [
                    {"source": "Archive", "excerpt": "letter 12"},
                    {"source": "Museum", "url": "https://example.org"},
                ],
            )
        assert len(saved) == 2
        assert {e.source for e in saved} == {"Archive", "Museum"}

        # Saving again REPLACES the set for this relationship (idempotent).
        async with session_scope(sessions) as session:
            replaced = await save_evidence(session, rel_id, [{"source": "Only archive"}])
        assert len(replaced) == 1

        async with sessions() as session:
            rows = (
                await session.execute(
                    select(Evidence).where(Evidence.relationship_id == rel_id)
                )
            ).scalars().all()
            assert len(rows) == 1 and rows[0].source == "Only archive"


async def test_save_evidence_unknown_relationship_is_not_found(open_db):
    async with open_db() as sessions:
        from app.repositories.evidence_repository import save_evidence

        async with session_scope(sessions) as session:
            with pytest.raises(NotFoundError) as excinfo:
                await save_evidence(session, "ghost-rel", [{"source": "A"}])

        message = str(excinfo.value)
        assert "ghost-rel" in message
        for leak in ("SELECT", "INSERT", "sqlite"):
            assert leak not in message


async def test_save_evidence_requires_a_source_for_every_item(open_db):
    async with open_db() as sessions:
        rel_id = await _seed_relationship(sessions)

        from app.repositories.evidence_repository import save_evidence

        async with session_scope(sessions) as session:
            with pytest.raises(ConflictError) as excinfo:
                await save_evidence(session, rel_id, [{"source": "   "}])

        message = str(excinfo.value)
        assert "source" in message.lower()
        assert "INSERT" not in message and "CHECK" not in message


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------


async def test_session_scope_rolls_back_on_body_error(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import create_analysis_run

        with pytest.raises(RuntimeError):
            async with session_scope(sessions) as session:
                await create_analysis_run(session, analysis_id="run-x")
                raise RuntimeError("body failed after a successful insert")

        async with sessions() as session:
            remaining = (
                await session.execute(
                    select(AnalysisRun).where(AnalysisRun.id == "run-x")
                )
            ).scalars().all()
            assert remaining == []  # the insert was rolled back


async def test_session_scope_commits_on_success(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import create_analysis_run

        async with session_scope(sessions) as session:
            await create_analysis_run(session, analysis_id="run-ok")

        async with sessions() as session:
            row = (
                await session.execute(
                    select(AnalysisRun).where(AnalysisRun.id == "run-ok")
                )
            ).scalars().one()
            assert row.status == "queued"


async def test_session_scope_translates_raw_database_errors(open_db):
    async with open_db() as sessions:
        with pytest.raises((ConflictError, DatabaseUnavailableError)) as excinfo:
            async with session_scope(sessions) as session:
                # Violates the status CHECK with a value straight through raw
                # SQL, so the translator (not a repository pre-check) must
                # catch it.
                await session.execute(
                    text(
                        "INSERT INTO analysis_runs (id, status) "
                        "VALUES ('run-raw', 'exploded')"
                    )
                )

        message = str(excinfo.value)
        for leak in ("INSERT", "CHECK", "exploded", "sqlite", "IntegrityError"):
            assert leak not in message


# ---------------------------------------------------------------------------
# sanitizer unit tests (the last line of defense)
# ---------------------------------------------------------------------------


def test_integrity_error_is_translated_without_driver_details():
    raw = IntegrityError(
        "INSERT INTO relationships (id) VALUES ('x')",
        {},
        Exception("sqlite3.IntegrityError: UNIQUE constraint failed: relationships.id"),
    )

    translated = translate_db_error(raw)

    assert isinstance(translated, ConflictError)
    assert not isinstance(translated, DatabaseUnavailableError)
    message = str(translated)
    for leak in ("UNIQUE", "INSERT", "sqlite3", "relationships"):
        assert leak not in message


def test_connection_failure_is_reported_as_unavailable():
    raw = OperationalError(
        "SELECT 1",
        {},
        Exception("sqlite3.OperationalError: unable to open database file"),
    )

    translated = translate_db_error(raw)

    assert isinstance(translated, DatabaseUnavailableError)
    message = str(translated)
    for leak in ("unable to open", "sqlite3", "SELECT", "OperationalError"):
        assert leak not in message


def test_unclassified_database_error_is_still_generic():
    raw = DBAPIError("SELECT * FROM secrets", {}, Exception("boom"))

    translated = translate_db_error(raw)

    assert type(translated) is RepositoryError  # generic but still sanitised
    message = str(translated)
    for leak in ("secrets", "boom", "SELECT"):
        assert leak not in message


# ---------------------------------------------------------------------------
# C4 — evidence service: supported / unsupported / ambiguous / contradicted
# ---------------------------------------------------------------------------


async def _propose_relationship(sessions, *, verification_status: str = "unverified") -> str:
    """Persist a model-proposed candidate edge (Ada -> Analytical Engine)."""
    source, target = await _seed_run_with_entities(sessions)
    saved = await _save_relationships(
        sessions,
        [
            {
                "source": source,
                "target": target,
                "label": "wrote notes for",
                "verificationStatus": verification_status,
            }
        ],
    )
    return saved[0].id


async def _add_records(sessions, records: list[dict]) -> None:
    async with sessions() as session:
        session.add_all(
            [
                HistoricalRecord(
                    id=record["id"],
                    title=record["title"],
                    source=record["source"],
                    content=record["content"],
                )
                for record in records
            ]
        )
        await session.commit()


async def _evaluate(sessions, relationship_id: str):
    from app.services.evidence_service import evaluate_relationship_evidence

    async with session_scope(sessions) as session:
        return await evaluate_relationship_evidence(session, relationship_id)


async def test_evidence_supporting_record_marks_verified(open_db):
    async with open_db() as sessions:
        relationship_id = await _propose_relationship(sessions)
        await _add_records(
            sessions,
            [
                {
                    "id": "src-1",
                    "title": "Notes of 1843",
                    "source": "Test Archive",
                    "content": (
                        "Ada Lovelace published her notes for the Analytical "
                        "Engine in 1843."
                    ),
                }
            ],
        )

        evaluation = await _evaluate(sessions, relationship_id)

        assert evaluation.status == "verified"
        assert evaluation.records_considered == 1
        assert evaluation.supporting == 1
        assert evaluation.contradicting == 0 and evaluation.inconclusive == 0

        assert len(evaluation.evidence) == 1
        link = evaluation.evidence[0]
        assert link.source == "Test Archive"  # traceable source on every link
        assert "Ada Lovelace" in link.excerpt and "Analytical Engine" in link.excerpt

        # Persisted: status, summary, notes and the evidence rows.
        from app.repositories.evidence_repository import list_evidence
        from app.repositories.relationship_repository import get_relationship

        async with sessions() as session:
            stored = await get_relationship(session, relationship_id)
            assert stored.verification_status == "verified"
            assert "Supported" in stored.summary
            assert any("Supporting: 1" in note for note in stored.notes)
            rows = await list_evidence(session, relationship_id)
            assert len(rows) == 1 and rows[0].source == "Test Archive"
            assert "Ada Lovelace" in (rows[0].excerpt or "")


async def test_evidence_missing_source_is_unverified_even_if_model_claimed_verified(open_db):
    """ACCEPTANCE: a model-proposed 'verified' must not survive without
    source material."""
    async with open_db() as sessions:
        relationship_id = await _propose_relationship(
            sessions, verification_status="verified"
        )

        evaluation = await _evaluate(sessions, relationship_id)

        assert evaluation.status == "unverified"  # downgraded, never verified
        assert evaluation.records_considered == 0
        assert evaluation.evidence == []  # missing evidence preserved explicitly
        assert "No source material" in evaluation.summary
        assert any("No records matched" in note for note in evaluation.notes)

        from app.repositories.relationship_repository import get_relationship

        async with sessions() as session:
            stored = await get_relationship(session, relationship_id)
            assert stored.verification_status == "unverified"
            assert "No source material" in stored.summary


async def test_evidence_irrelevant_records_mark_unsupported(open_db):
    async with open_db() as sessions:
        relationship_id = await _propose_relationship(sessions)
        await _add_records(
            sessions,
            [
                {
                    "id": "src-ada",
                    "title": "Correspondence",
                    "source": "Test Archive",
                    "content": (
                        "Ada Lovelace corresponded with Charles Babbage "
                        "throughout the 1830s."
                    ),
                },
                {
                    "id": "src-engine",
                    "title": "Build status",
                    "source": "Other Archive",
                    "content": (
                        "The Analytical Engine remained unbuilt in Babbage's "
                        "lifetime."
                    ),
                },
            ],
        )

        evaluation = await _evaluate(sessions, relationship_id)

        assert evaluation.status == "unsupported"
        assert evaluation.records_considered == 2
        assert evaluation.supporting == 0
        assert "none documents" in evaluation.summary

        # The considered (irrelevant) excerpts are preserved, not hidden.
        assert len(evaluation.evidence) == 2
        for link in evaluation.evidence:
            assert link.excerpt
            assert "Ada Lovelace" in link.excerpt or "Analytical Engine" in link.excerpt

        from app.repositories.relationship_repository import get_relationship

        async with sessions() as session:
            stored = await get_relationship(session, relationship_id)
            assert stored.verification_status == "unsupported"


async def test_evidence_contradictory_record_marks_disputed_and_beats_support(open_db):
    async with open_db() as sessions:
        relationship_id = await _propose_relationship(sessions)
        await _add_records(
            sessions,
            [
                {
                    "id": "src-support",
                    "title": "Articles of 1843",
                    "source": "Test Archive",
                    "content": (
                        "Ada Lovelace's 1843 notes accompanied the Analytical "
                        "Engine articles."
                    ),
                },
                {
                    "id": "src-contra",
                    "title": "Attribution debate",
                    "source": "Critical Review",
                    "content": (
                        "The claim that Ada Lovelace wrote notes for the "
                        "Analytical Engine is disputed by modern historians."
                    ),
                },
            ],
        )

        evaluation = await _evaluate(sessions, relationship_id)

        assert evaluation.status == "disputed"  # contradiction beats support
        assert evaluation.supporting == 1 and evaluation.contradicting == 1
        assert any("contradiction takes precedence" in note for note in evaluation.notes)

        # Only the contradicting excerpt becomes evidence — and it shows the cue.
        assert len(evaluation.evidence) == 1
        assert evaluation.evidence[0].source == "Critical Review"
        assert "disputed" in evaluation.evidence[0].excerpt.casefold()

        from app.repositories.relationship_repository import get_relationship

        async with sessions() as session:
            stored = await get_relationship(session, relationship_id)
            assert stored.verification_status == "disputed"


async def test_evidence_distant_cooccurrence_stays_unverified(open_db):
    """Ambiguous case: both names appear, but never in a connected context."""
    async with open_db() as sessions:
        relationship_id = await _propose_relationship(sessions)
        await _add_records(
            sessions,
            [
                {
                    "id": "src-far",
                    "title": "Long register",
                    "source": "Test Archive",
                    "content": (
                        "Ada Lovelace "
                        + "y" * 400
                        + " the Analytical Engine appears separately much later."
                    ),
                }
            ],
        )

        evaluation = await _evaluate(sessions, relationship_id)

        assert evaluation.status == "unverified"  # ambiguous, not verified
        assert evaluation.inconclusive == 1
        assert "Inconclusive" in evaluation.summary
        assert len(evaluation.evidence) == 1  # the ambiguous record is preserved

        from app.repositories.relationship_repository import get_relationship

        async with sessions() as session:
            stored = await get_relationship(session, relationship_id)
            assert stored.verification_status == "unverified"


async def test_set_relationship_verification_rejects_unknown_status(open_db):
    """Vocabulary guard lives in the repository, not only in the service
    (and runs before any database touch)."""
    from app.repositories.relationship_repository import set_relationship_verification

    async with open_db() as sessions:
        async with sessions() as session:
            with pytest.raises(ConflictError) as excinfo:
                await set_relationship_verification(
                    session, "missing-rel", verification_status="truthy"
                )

    message = str(excinfo.value)
    assert "truthy" in message and "verified" in message
    assert "CHECK" not in message and "sqlite" not in message


async def test_evaluate_analysis_evidence_covers_every_relationship(open_db):
    async with open_db() as sessions:
        from app.repositories.analysis_repository import create_analysis_run
        from app.repositories.entity_repository import upsert_entities
        from app.services.evidence_service import evaluate_analysis_evidence

        async with sessions() as session:
            await create_analysis_run(session, analysis_id="run-ev")
            entities = await upsert_entities(
                session,
                [
                    {"name": "Ada Lovelace", "entity_type": "person"},
                    {"name": "Analytical Engine", "entity_type": "event"},
                    {"name": "Charles Babbage", "entity_type": "person"},
                ],
            )
            await session.commit()
        ada, engine, babbage = (entity.id for entity in entities)

        supported = (
            await _save_relationships(
                sessions,
                [{"source": ada, "target": engine, "label": "wrote notes for"}],
                run_id="run-ev",
            )
        )[0]
        unsupported = (
            await _save_relationships(
                sessions,
                [{"source": ada, "target": babbage, "label": "corresponded with"}],
                run_id="run-ev",
            )
        )[0]
        await _add_records(
            sessions,
            [
                {
                    "id": "src-ev",
                    "title": "Notes of 1843",
                    "source": "Test Archive",
                    "content": (
                        "Ada Lovelace published her notes for the Analytical "
                        "Engine in 1843."
                    ),
                }
            ],
        )

        async with session_scope(sessions) as session:
            evaluations = await evaluate_analysis_evidence(session, "run-ev")

        assert len(evaluations) == 2
        by_id = {item.relationship_id: item for item in evaluations}
        assert by_id[supported.id].status == "verified"
        assert by_id[unsupported.id].status == "unsupported"
        assert by_id[unsupported.id].records_considered == 1  # Ada's record only


async def test_evidence_database_failure_is_sanitized(tmp_path):
    """A broken database produces DatabaseUnavailableError, never raw SQL
    or driver text, anywhere in the evaluation path."""
    from app.db.session import create_engine, create_session_factory
    from app.services.evidence_service import evaluate_relationship_evidence

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/no-such-dir/x.db")
    sessions = create_session_factory(engine)
    try:
        async with sessions() as session:
            with pytest.raises(DatabaseUnavailableError) as excinfo:
                await evaluate_relationship_evidence(session, "any-rel")
    finally:
        await engine.dispose()

    message = str(excinfo.value)
    assert "unavailable" in message.casefold()
    for leak in ("sqlite", "SELECT", "OperationalError", str(tmp_path)):
        assert leak not in message
