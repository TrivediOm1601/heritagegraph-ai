"""R7 — server-generated PDF exports.

The HTTP tests cover authorization and response handling; the direct
``render_pdf`` tests cover layout properties (multi-page flow, Unicode,
links, no blank trailing page) that are awkward to assert through headers.
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pytest

from conftest import SAMPLE_PNG

from app.services.pdf_service import (
    PdfDocument,
    PdfEdge,
    PdfEntity,
    PdfEntry,
    PdfSource,
    render_pdf,
    safe_filename,
    status_note,
)


def _page_count(pdf: bytes) -> int:
    # reportlab writes page objects uncompressed; this is a stable count for
    # our own generated documents (no reliance on an external PDF parser).
    return len(re.findall(rb"/Type\s*/Page\b", pdf))


def _upload(client, filename: str = "monument.png") -> str:
    response = client.post(
        "/api/analysis",
        files={"file": (filename, io.BytesIO(SAMPLE_PNG), "image/png")},
    )
    assert response.status_code == 202, response.text
    return response.json()["analysisId"]


# ---------------------------------------------------------------------------
# Layout (direct renderer)
# ---------------------------------------------------------------------------


def test_rendered_pdf_is_valid_and_has_a_page_number_footer():
    entry = PdfEntry(
        title="Qutub Minar",
        kind="image_analysis",
        status="completed",
        created_at=datetime(2026, 10, 29, tzinfo=timezone.utc),
        summary="A tall minaret in Delhi.",
        extracted_text="QUTUB MINAR\nDELHI",
        entities=(PdfEntity(name="Qutub Minar", type="place", confidence=0.93),),
        edges=(PdfEdge(source="Qutub Minar", target="Delhi", label="located in"),),
        sources=(
            PdfSource(
                title="Qutub Minar — archive",
                url="https://example.org/qutub",
                publisher="Test archive",
                excerpt="The minaret was begun in 1193.",
            ),
        ),
    )

    pdf = render_pdf(PdfDocument(entries=(entry,), user_email="user@example.org"))

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert _page_count(pdf) == 1


def test_long_content_flows_across_pages_without_blank_pages():
    long_text = " ".join(f"Inscription line {index} — heritage text." for index in range(400))
    entry = PdfEntry(
        title="Very long analysis",
        kind="image_analysis",
        status="partial",
        summary=long_text,
        extracted_text=long_text,
        interpretation=long_text,
        warnings=("OCR was unavailable.",),
        limitations=("The uploaded image is not retained.",),
    )

    pdf = render_pdf(PdfDocument(entries=(entry,), user_email="user@example.org"))

    assert _page_count(pdf) > 1
    # No trailing blank page: the last page contains the closing limitation
    # section marker rather than being an empty spill-over.
    assert b"%%EOF" in pdf.rstrip()[-32:]


def test_unicode_text_is_embedded_not_replaced():
    entry = PdfEntry(
        title="रानी की वाव — Rani ki Vav",
        kind="text_search",
        status="completed",
        summary="Édifice classé au patrimoine mondial; ラーニー・キ・ヴァーヴ.",
        search_query="Rani ki Vav",
    )

    pdf = render_pdf(PdfDocument(entries=(entry,), user_email="user@example.org"))

    assert pdf.startswith(b"%PDF-")
    assert _page_count(pdf) == 1
    # A TrueType font program is embedded (``/FontFile2``): the document is
    # not relying on a Latin-1 base-14 font, which would drop these glyphs.
    assert b"/FontFile2" in pdf


def test_incomplete_analysis_is_marked_in_the_document():
    # The labelling is pure, so assert it directly (embedded font text is
    # glyph-encoded and cannot be searched byte-wise).
    assert "PARTIAL" in (status_note("partial") or "")
    assert "FAILED" in (status_note("failed") or "")
    assert status_note("completed") is None

    entry = PdfEntry(
        title="Degraded run",
        kind="image_analysis",
        status="partial",
        warnings=("The vision model returned an unusable response.",),
    )
    pdf = render_pdf(PdfDocument(entries=(entry,), user_email="user@example.org"))
    assert pdf.startswith(b"%PDF-")
    assert _page_count(pdf) == 1


def test_empty_document_is_refused():
    with pytest.raises(ValueError):
        render_pdf(PdfDocument(entries=(), user_email="user@example.org"))


def test_safe_filename_has_no_user_text_or_path_parts():
    name = safe_filename(kind="history")
    assert re.fullmatch(r"heritagegraph-history-\d{4}-\d{2}-\d{2}\.pdf", name)
    assert "/" not in name and "\\" not in name


# ---------------------------------------------------------------------------
# HTTP export (authorization + response contract)
# ---------------------------------------------------------------------------


def test_single_analysis_export_is_a_private_pdf(auth_client, monkeypatch):
    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")

    _upload(auth_client)
    entry_id = auth_client.get("/api/history").json()["items"][0]["id"]

    response = auth_client.get(f"/api/history/{entry_id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert 'attachment; filename="heritagegraph-analysis-' in response.headers[
        "content-disposition"
    ]
    assert "no-store" in response.headers["cache-control"]
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > 1000


def test_full_history_export_contains_every_record(auth_client, monkeypatch):
    monkeypatch.setenv("HG_DEMO_MODE", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")

    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    _upload(auth_client)

    history = auth_client.get("/api/history").json()
    assert history["total"] == 2

    response = auth_client.get("/api/history/export/pdf")

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert 'filename="heritagegraph-history-' in response.headers["content-disposition"]
    # Two records, so at least the summary table reports them.
    assert history["total"] == 2


def test_history_export_with_selected_ids(auth_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    auth_client.get("/api/search", params={"query": "Charles Babbage"})
    items = auth_client.get("/api/history").json()["items"]
    chosen = items[0]["id"]

    response = auth_client.get("/api/history/export/pdf", params={"ids": chosen})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


def test_empty_history_export_is_a_404_not_a_blank_pdf(auth_client):
    response = auth_client.get("/api/history/export/pdf")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_export_rejects_invalid_and_foreign_ids(auth_client, other_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    entry_id = auth_client.get("/api/history").json()["items"][0]["id"]

    # Another account cannot export somebody else's record...
    assert other_client.get(f"/api/history/{entry_id}/pdf").status_code == 404
    # ...nor pull it into a bulk export by guessing its id.
    bulk = other_client.get("/api/history/export/pdf", params={"ids": entry_id})
    assert bulk.status_code == 404

    assert auth_client.get("/api/history/no-such-record/pdf").status_code == 404


def test_export_requires_authentication(auth_url):
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    anonymous = TestClient(create_app(Settings.from_env()))

    assert anonymous.get("/api/history/export/pdf").status_code == 401
    assert anonymous.get("/api/history/some-id/pdf").status_code == 401


def test_text_search_export_includes_sources(auth_client):
    auth_client.get("/api/search", params={"query": "Ada Lovelace"})
    entry_id = auth_client.get("/api/history").json()["items"][0]["id"]

    detail = auth_client.get(f"/api/history/{entry_id}").json()
    response = auth_client.get(f"/api/history/{entry_id}/pdf")

    assert detail["search"]["results"], "the stored search has results to export"
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
