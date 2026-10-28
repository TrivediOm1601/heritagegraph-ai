"""R7 — truthful analysis statuses (completed / partial / failed).

The original bug report: the UI showed "Completed" even though OCR was
unconfigured **and** the vision model returned no text — i.e. a run with no
usable result was reported as success. These tests pin the grading rules.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.analysis_service import terminal_status
from app.services.extraction_service import ExtractionResult


def _extraction(**overrides) -> ExtractionResult:
    base = dict(
        extracted_text=None,
        entities=[],
        candidates=[],
        warnings=[],
        interpretation=None,
        relationships=[],
        degraded=False,
        vision_ok=True,
        ocr_attempted=False,
        ocr_provider=None,
    )
    base.update(overrides)
    return ExtractionResult(**base)


def test_successful_vision_without_ocr_is_completed():
    """OCR is optional; a vision-only analysis with a usable result is complete."""
    from app.schemas.entity import AnalysisEntity

    entity = AnalysisEntity(id="ent-1", name="Qutub Minar", type="place")
    assert (
        terminal_status(
            _extraction(vision_ok=True, entities=[entity], extracted_text=None)
        )
        == "completed"
    )


def test_degraded_channel_with_results_is_partial():
    """A configured channel that failed degrades the run to partial."""
    assert (
        terminal_status(
            _extraction(vision_ok=True, degraded=True, extracted_text="text")
        )
        == "partial"
    )


def test_vision_failure_with_ocr_text_is_partial():
    """Some useful output survives, so the run is not a total failure."""
    assert (
        terminal_status(_extraction(vision_ok=False, extracted_text="OCR TEXT"))
        == "partial"
    )


def test_no_usable_output_is_failed_not_completed():
    """This is the exact false-success case from the bug report."""
    assert (
        terminal_status(_extraction(vision_ok=False, extracted_text=None)) == "failed"
    )
    assert (
        terminal_status(_extraction(vision_ok=False, extracted_text="   ")) == "failed"
    )


def test_entities_without_text_are_a_completed_analysis():
    """No readable text is normal for a photograph; identified entities
    make it a complete analysis."""
    from app.schemas.entity import AnalysisEntity

    entity = AnalysisEntity(id="ent-1", name="Qutub Minar", type="place")
    assert (
        terminal_status(
            _extraction(vision_ok=True, entities=[entity], extracted_text=None)
        )
        == "completed"
    )


def test_pipeline_that_found_nothing_is_partial_not_completed():
    """The reported false-success case: OCR unconfigured, vision returned no
    text and no entities. It must not be reported as a completed analysis."""
    assert (
        terminal_status(_extraction(vision_ok=True, entities=[], extracted_text=None))
        == "partial"
    )


def test_run_analysis_persists_partial_when_a_stage_degrades(auth_url, monkeypatch):
    """The stored run status (and its history row) say partial, not completed."""
    from sqlalchemy import select

    from conftest import SAMPLE_PNG

    from app.db.models import HistoryEntry
    from app.db.session import create_engine, create_session_factory, session_scope
    from app.repositories.analysis_repository import create_analysis_run
    from app.repositories.history_repository import create_entry
    from app.repositories.user_repository import create_user
    from app.services import analysis_service

    async def _degraded_extraction(*args, **kwargs):
        return _extraction(
            vision_ok=True,
            degraded=True,
            extracted_text="PARTIAL TEXT",
            warnings=["The vision model returned an unusable response."],
        )

    monkeypatch.setattr(analysis_service, "run_extraction", _degraded_extraction)

    async def _run() -> str:
        engine = create_engine(auth_url)
        factory = create_session_factory(engine)
        try:
            async with session_scope(factory) as session:
                owner = await create_user(
                    session, email="status@example.org", password_hash="x"
                )
                await create_analysis_run(
                    session, analysis_id="run-partial", user_id=owner.id
                )
                await create_entry(
                    session,
                    user_id=owner.id,
                    kind="image_analysis",
                    analysis_id="run-partial",
                    status="queued",
                )

            await analysis_service.run_analysis(
                SAMPLE_PNG,
                "degraded.png",
                "image/png",
                analysis_id="run-partial",
            )

            async with session_scope(factory) as session:
                row = await session.scalar(select(HistoryEntry))
                return row.status
        finally:
            await engine.dispose()

    assert asyncio.run(_run()) == "partial"
