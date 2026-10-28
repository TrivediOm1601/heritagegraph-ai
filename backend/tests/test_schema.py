"""C1 tests: model imports, migration initialization, FK behaviour, constraints.

Every schema-level test runs against a database created by the **real
Alembic migrations** (``database/migrations/versions/`` — 0001 plus the
reviewed 0002/0003 additive repairs),
not ``Base.metadata.create_all`` — so the migration itself, not just the
models, is what gets verified.

SQLite powers these tests (no server in this environment); the migration
is dialect-portable by construction, and its PostgreSQL compilation is
checked separately in the DDL smoke test.
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import ConfigurationError, reset_settings_cache
from app.db.init_db import ALEMBIC_INI, alembic_config, init_database
from app.db.models import (
    ENTITY_TYPES,
    AnalysisEntity,
    AnalysisRun,
    Base,
    Entity,
    Evidence,
    HistoricalRecord,
    Relationship,
)

# The six entities required by C1 — the model module must define exactly these.
REQUIRED_TABLES = {
    "entities",
    "historical_records",
    "relationships",
    "evidence",
    "analysis_runs",
    "analysis_entities",
    # R7 — accounts, sessions and personal history.
    "users",
    "user_sessions",
    "history_entries",
}


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def migrated_url(tmp_path) -> str:
    """A per-test database migrated by the real Alembic pipeline.

    Per-test (not session-scoped): tests that commit real rows must not
    contaminate later constraint assertions, and re-running the migration
    for every test also keeps the migration itself under test.
    """
    url = f"sqlite:///{tmp_path / 'schema.db'}"
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def migrated_engine(migrated_url):
    engine = create_engine(migrated_url)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    yield engine
    engine.dispose()


@pytest.fixture
def session(migrated_engine):
    bound = sessionmaker(bind=migrated_engine)()
    yield bound
    bound.rollback()
    bound.close()


def _seed_run(session) -> AnalysisRun:
    run = AnalysisRun(status="queued")
    session.add(run)
    session.flush()
    return run


def _seed_entity(session, name: str, entity_type: str = "person") -> Entity:
    entity = Entity(name=name, entity_type=entity_type)
    session.add(entity)
    session.flush()
    return entity


def _seed_relationship(session, run: AnalysisRun, source: Entity, target: Entity, **kw) -> Relationship:
    relationship = Relationship(
        analysis_id=run.id,
        source_entity_id=source.id,
        target_entity_id=target.id,
        **kw,
    )
    session.add(relationship)
    session.flush()
    return relationship


# ---------------------------------------------------------------------------
# Model imports
# ---------------------------------------------------------------------------


def test_models_import_and_define_exactly_the_required_tables():
    assert set(Base.metadata.tables) == REQUIRED_TABLES
    # Vocabularies imported from the frozen API contract, not redefined.
    assert set(ENTITY_TYPES) >= {"person", "place", "event", "date"}


# ---------------------------------------------------------------------------
# Migration initialization
# ---------------------------------------------------------------------------


def test_migration_creates_all_tables_and_stamps_head(migrated_url, migrated_engine):
    tables = set(inspect(migrated_engine).get_table_names())

    assert REQUIRED_TABLES <= tables
    assert "alembic_version" in tables

    with migrated_engine.connect() as connection:
        applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    head = ScriptDirectory.from_config(alembic_config(migrated_url)).get_current_head()
    # Head of the frozen chain: 0001 initial schema + the reviewed,
    # additive repairs (0002 record URLs, 0003 interpretation, 0004
    # briefing) + R7 (0005 users/sessions/history).
    assert applied == head == "0005_users_sessions_history"


def test_migration_upgrade_is_idempotent(migrated_url):
    # Running the same upgrade again must be a no-op, not an error.
    command.upgrade(alembic_config(migrated_url), "head")
    command.upgrade(alembic_config(migrated_url), "head")


def test_migration_output_matches_the_models(migrated_engine):
    """The frozen migration and the models must not drift apart."""
    inspector = inspect(migrated_engine)

    for table_name in sorted(REQUIRED_TABLES):
        table = Base.metadata.tables[table_name]
        db_columns = {column["name"]: column for column in inspector.get_columns(table_name)}

        assert set(db_columns) == set(table.columns.keys()), table_name
        for name, column in table.columns.items():
            assert db_columns[name]["nullable"] == column.nullable, f"{table_name}.{name}"

        db_pk = set(inspector.get_pk_constraint(table_name)["constrained_columns"])
        meta_pk = {column.name for column in table.columns if column.primary_key}
        assert db_pk == meta_pk, table_name

        db_fks = {
            (column, f"{fk['referred_table']}.{fk['referred_columns'][0]}")
            for fk in inspector.get_foreign_keys(table_name)
            for column in fk["constrained_columns"]
        }
        meta_fks = {
            (fk.parent.name, fk.target_fullname) for fk in table.foreign_keys
        }
        assert db_fks == meta_fks, table_name


def test_init_database_runs_the_migrations_at_the_r7_head(tmp_path):
    url = f"sqlite:///{tmp_path / 'init.db'}"

    revision = init_database(url)

    assert revision == "0005_users_sessions_history"
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert REQUIRED_TABLES <= tables
        with engine.connect() as connection:
            applied = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert applied == revision
    finally:
        engine.dispose()


def test_init_database_refuses_to_guess_a_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_settings_cache()

    with pytest.raises(ConfigurationError, match="DATABASE_URL is not set"):
        init_database()


def test_migration_downgrade_removes_the_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'down.db'}"
    config = alembic_config(url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    engine = create_engine(url)
    try:
        remaining = set(inspect(engine).get_table_names())
        assert not (REQUIRED_TABLES & remaining)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Foreign-key behaviour
# ---------------------------------------------------------------------------


def test_relationship_requires_existing_analysis(session):
    source = _seed_entity(session, "Ada Lovelace")
    target = _seed_entity(session, "Analytical Engine")
    session.add(
        Relationship(
            analysis_id="missing-run",
            source_entity_id=source.id,
            target_entity_id=target.id,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_relationship_requires_existing_entities(session):
    run = _seed_run(session)
    existing = _seed_entity(session, "Ada Lovelace")
    session.add(
        Relationship(
            analysis_id=run.id,
            source_entity_id=existing.id,
            target_entity_id="missing-entity",
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_evidence_requires_existing_relationship(session):
    session.add(Evidence(relationship_id="missing-rel", source="Letter, 1843"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_analysis_entities_require_existing_rows(session):
    run = _seed_run(session)
    session.add(AnalysisEntity(analysis_id=run.id, entity_id="missing-entity", confidence=0.9))

    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_an_analysis_cascades_children_but_keeps_canonical_entities(session):
    run = _seed_run(session)
    ada = _seed_entity(session, "Ada Lovelace")
    engine_entity = _seed_entity(session, "Analytical Engine", entity_type="other")
    relationship = _seed_relationship(session, run, ada, engine_entity)
    session.add(Evidence(relationship_id=relationship.id, source="Letter, 1843"))
    session.add(AnalysisEntity(analysis_id=run.id, entity_id=ada.id, confidence=0.94))
    session.commit()

    session.delete(run)
    session.commit()

    assert session.scalars(select(Relationship)).all() == []
    assert session.scalars(select(Evidence)).all() == []
    assert session.scalars(select(AnalysisEntity)).all() == []
    # The canonical entity store is global and survives an analysis deletion.
    assert len(session.scalars(select(Entity)).all()) == 2


# ---------------------------------------------------------------------------
# Constraint violations
# ---------------------------------------------------------------------------


def test_unknown_verification_status_is_rejected(session):
    run = _seed_run(session)
    source = _seed_entity(session, "Ada Lovelace")
    target = _seed_entity(session, "Analytical Engine")

    with pytest.raises(IntegrityError):
        _seed_relationship(session, run, source, target, verification_status="trusted")


def test_relationship_defaults_to_unverified_never_verified(session):
    run = _seed_run(session)
    source = _seed_entity(session, "Ada Lovelace")
    target = _seed_entity(session, "Analytical Engine", entity_type="other")

    # Raw insert: proves the *database* default (not an ORM-side default).
    session.execute(
        text(
            "INSERT INTO relationships (id, analysis_id, source_entity_id, target_entity_id) "
            "VALUES ('rel-raw', :run, :source, :target)"
        ),
        {"run": run.id, "source": source.id, "target": target.id},
    )
    status = session.execute(
        text("SELECT verification_status FROM relationships WHERE id = 'rel-raw'")
    ).scalar()

    assert status == "unverified"


def test_unknown_entity_type_is_rejected(session):
    session.add(Entity(name="Mysterious", entity_type="prophecy"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_unknown_analysis_status_is_rejected(session):
    session.add(AnalysisRun(status="paused"))

    with pytest.raises(IntegrityError):
        session.flush()


def test_progress_and_confidence_ranges_are_enforced(session):
    session.add(AnalysisRun(status="processing", progress=1.5))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    run = _seed_run(session)
    entity = _seed_entity(session, "Ada Lovelace")
    session.add(AnalysisEntity(analysis_id=run.id, entity_id=entity.id, confidence=-0.2))
    with pytest.raises(IntegrityError):
        session.flush()


def test_entity_identity_is_unique_per_name_and_type(session):
    _seed_entity(session, "Ada Lovelace", "person")

    session.add(Entity(name="Ada Lovelace", entity_type="person"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # Same name, different type is a different canonical entity.
    session.add(Entity(name="Ada Lovelace", entity_type="place"))
    session.flush()


def test_relationship_pair_is_unique_per_analysis(session):
    run = _seed_run(session)
    source = _seed_entity(session, "Ada Lovelace")
    target = _seed_entity(session, "Analytical Engine")
    _seed_relationship(session, run, source, target)

    session.add(
        Relationship(
            analysis_id=run.id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            label="duplicate edge",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_self_loops_are_rejected(session):
    run = _seed_run(session)
    ada = _seed_entity(session, "Ada Lovelace")

    with pytest.raises(IntegrityError):
        _seed_relationship(session, run, ada, ada)


def test_required_fields_are_enforced(session):
    session.add(HistoricalRecord(title="Record without a source"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # A real relationship, so the only thing that can fail is NOT NULL.
    run = _seed_run(session)
    source = _seed_entity(session, "Ada Lovelace")
    target = _seed_entity(session, "Analytical Engine")
    relationship = _seed_relationship(session, run, source, target)
    session.add(Evidence(relationship_id=relationship.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_record_title_is_unique_per_source(session):
    session.add(HistoricalRecord(title="Notes on the Analytical Engine", source="Science Museum"))
    session.flush()

    session.add(HistoricalRecord(title="Notes on the Analytical Engine", source="Science Museum"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    # Same title from a different source is a legitimately distinct record.
    session.add(HistoricalRecord(title="Notes on the Analytical Engine", source="British Library"))
    session.flush()
