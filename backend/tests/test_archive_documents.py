"""Document digitisation tests for the Heritage Archive Studio.

These are unit tests over :mod:`app.services.document_service` — no HTTP and
no network. Scanned-PDF OCR is exercised with a mocked provider transport, so
the test proves the *pipeline* wiring, not that a real OCR provider works.
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import httpx
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from app.integrations import ProviderConfigurationError
from app.services.document_service import (
    ExtractionError,
    detect_language,
    extract_document,
    text_fingerprint,
)
from app.services.zip_service import ZipLimits, ZipSafetyError, inspect_zip


def make_pdf(pages: list[str]) -> bytes:
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer)
    for text in pages:
        document.drawString(72, 760, text)
        document.showPage()
    document.save()
    return buffer.getvalue()


def make_docx(*, with_text: bool = True) -> bytes:
    body = (
        """
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
          <w:r><w:t>Rani ki Vav</w:t></w:r></w:p>
        <w:p><w:r><w:t>The stepwell was built in the 11th century.</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
          <w:r><w:t>Inscriptions</w:t></w:r></w:p>
        <w:p><w:r><w:t>Carved panels line the walls.</w:t></w:r></w:p>
        """
        if with_text
        else "<w:p><w:r><w:t></w:t></w:r></w:p>"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def make_pptx(slides: list[tuple[str, str]], *, notes: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (title, body) in enumerate(slides, start=1):
            xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                "<p:cSld><p:spTree>"
                f"<p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p>"
                f"<a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            )
            archive.writestr(f"ppt/slides/slide{index}.xml", xml)
            if notes:
                note_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
                    f"<p:csld><p:spTree><p:sp><p:txBody><a:p><a:r>"
                    f"<a:t>Speaker note {index}</a:t></a:r></a:p></p:txBody></p:sp>"
                    "</p:spTree></p:csld></p:notes>"
                )
                archive.writestr(f"ppt/notesSlides/notesSlide{index}.xml", note_xml)
        archive.writestr(
            "ppt/media/image1.png",
            b"\x89PNG\r\n\x1a\n" + b"0" * 32,
        )
    return buffer.getvalue()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def test_pdf_text_is_extracted_per_page():
    pdf = make_pdf(["Page one: Rani ki Vav", "Page two: Adalaj Stepwell"])
    result = run(extract_document(pdf, filename="report.pdf", kind="pdf"))

    assert [page.page_number for page in result.pages] == [1, 2]
    assert "Rani ki Vav" in result.pages[0].text
    assert "Adalaj Stepwell" in result.pages[1].text
    assert result.pages[0].source == "text-layer"
    assert result.metadata["page_count"] == 2
    assert result.page_label == "Page"
    assert result.has_text


def test_pdf_with_no_text_layer_is_ocrd_when_a_provider_answers(monkeypatch):
    pdf = make_pdf([""])

    def fake_render(_data, _index):
        buffer = io.BytesIO()
        Image.new("RGB", (40, 20), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    async def fake_ocr(*_args, **_kwargs):
        from app.integrations.ocr import OcrResult

        return OcrResult(text="RANI KI VAV", engine="fixture", provenance="injected-client")

    monkeypatch.setattr("app.services.document_service._render_pdf_page", fake_render)
    monkeypatch.setattr("app.services.document_service.run_ocr", fake_ocr)

    result = run(extract_document(pdf, filename="scan.pdf", kind="pdf"))

    assert result.pages[0].text == "RANI KI VAV"
    assert result.pages[0].source == "ocr"
    assert result.metadata["scanned_pages"] == 1


def test_scanned_pdf_without_ocr_configuration_stays_empty_with_a_warning(monkeypatch):
    pdf = make_pdf([""])

    def fake_render(_data, _index):
        return b"not-empty"

    async def fake_ocr(*_args, **_kwargs):
        raise ProviderConfigurationError(
            "OCR is not configured: set OCR_SPACE_API_KEY", provider="OCR service"
        )

    monkeypatch.setattr("app.services.document_service._render_pdf_page", fake_render)
    monkeypatch.setattr("app.services.document_service.run_ocr", fake_ocr)

    result = run(extract_document(pdf, filename="scan.pdf", kind="pdf"))

    assert result.pages[0].text == ""
    assert result.pages[0].source == "none"
    assert any("OCR skipped" in warning for warning in result.warnings)
    assert result.has_text is False


def test_corrupt_pdf_is_rejected_not_read_as_text():
    with pytest.raises(ExtractionError):
        run(extract_document(b"%PDF-1.4 broken", filename="broken.pdf", kind="pdf"))


# ---------------------------------------------------------------------------
# DOCX / PPTX
# ---------------------------------------------------------------------------


def test_docx_keeps_heading_sections_and_tables():
    result = run(extract_document(make_docx(), filename="notes.docx", kind="docx"))

    assert result.page_label == "Section"
    assert result.page_count == 2
    assert "Rani ki Vav" in result.pages[0].text
    assert "11th century" in result.pages[0].text
    assert "Inscriptions" in result.pages[1].text
    assert result.has_text


def test_docx_with_only_images_has_no_text():
    result = run(extract_document(make_docx(with_text=False), filename="images.docx", kind="docx"))
    assert result.has_text is False
    assert any("No text could be extracted" in warning for warning in result.warnings)


def test_pptx_extracts_slides_and_speaker_notes():
    result = run(
        extract_document(
            make_pptx([("Sun Temple", "Modhera, 11th century"), ("Stepwells", "Water architecture")]),
            filename="deck.pptx",
            kind="pptx",
        )
    )

    assert [page.page_number for page in result.pages] == [1, 2]
    assert "Sun Temple" in result.pages[0].text
    assert "Speaker note 1" in result.pages[0].text
    assert result.metadata["embedded_images"] == 1
    assert any("embedded image" in warning for warning in result.warnings)


def test_zip_named_docx_is_rejected_as_a_word_document():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("random.txt", "hello")
    with pytest.raises(ExtractionError):
        run(extract_document(buffer.getvalue(), filename="notreally.docx", kind="docx"))


# ---------------------------------------------------------------------------
# Text / Markdown / language
# ---------------------------------------------------------------------------


def test_markdown_sections_and_character_preservation():
    markdown = (
        "# Stepwells of Gujarat\n\n"
        "The **Rani ki Vav** at Paṭan is a stepwell.\n\n"
        "## Adalaj\n\nBuilt in 1499 CE.\n"
    ).encode("utf-8")
    result = run(extract_document(markdown, filename="notes.md", kind="text"))

    assert result.page_count == 2
    assert "Stepwells of Gujarat" in result.pages[0].text
    assert "Paṭan" in result.pages[0].text  # diacritics preserved
    assert "**" not in result.pages[0].text  # markup stripped
    assert result.metadata["encoding"] in {"utf-8", "utf-8-sig"}


def test_language_detection_reports_the_script_not_a_guess():
    assert detect_language("રાણી કી વાવ")[0] == "gu"
    assert detect_language("रानी की वाव")[1] == "Devanagari"
    assert detect_language("Rani ki Vav")[0] == "und-Latn"


def test_text_fingerprint_ignores_punctuation_and_case():
    assert text_fingerprint("Rani ki Vav, Patan.") == text_fingerprint("rani  KI vav patan")


# ---------------------------------------------------------------------------
# ZIP safety
# ---------------------------------------------------------------------------


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_zip_path_traversal_is_rejected():
    with pytest.raises(ZipSafetyError):
        inspect_zip(make_zip({"../escape.txt": b"nope"}))


def test_zip_absolute_path_is_rejected():
    with pytest.raises(ZipSafetyError):
        inspect_zip(make_zip({"/etc/passwd": b"nope"}))


def test_zip_accepts_supported_members_and_skips_others():
    inspection = inspect_zip(
        make_zip(
            {
                "notes/history.txt": b"Rani ki Vav",
                "notes/scan.pdf": b"%PDF-1.4 data",
                "tools/run.exe": b"MZ",
                "nested.zip": b"PK\x03\x04",
            }
        )
    )

    assert [member.filename for member in inspection.members] == ["history.txt", "scan.pdf"]
    assert {name for name, _ in inspection.rejected} == {"tools/run.exe", "nested.zip"}
    assert len(inspection.warnings) == 1


def test_zip_entry_count_limit_rejects_the_archive():
    entries = {f"file-{index}.txt": b"x" for index in range(5)}
    with pytest.raises(ZipSafetyError) as excinfo:
        inspect_zip(make_zip(entries), limits=ZipLimits(max_entries=3))
    assert "more than" in str(excinfo.value)


def test_zip_constructor_rejects_an_empty_archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w"):
        pass
    with pytest.raises(ZipSafetyError):
        inspect_zip(buffer.getvalue())


def test_zip_ocr_client_is_threaded_to_the_provider(monkeypatch):
    """The OCR client seam reaches the provider (no real network call)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == "test-key"
        return httpx.Response(
            200,
            json={
                "IsErroredOnProcessing": False,
                "OCRExitCode": 1,
                "ParsedResults": [{"ParsedText": "SANCHI STUPA"}],
            },
        )

    monkeypatch.setenv("OCR_PROVIDER", "ocr_space")
    monkeypatch.setenv("OCR_SPACE_API_KEY", "test-key")

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await extract_document(
                make_pdf([""]),
                filename="scan.pdf",
                kind="pdf",
                ocr_client=client,
            )
        finally:
            await client.aclose()

    # Rendering is real (pypdfium2 is installed); the provider is mocked.
    result = run(scenario())
    assert result.pages[0].text == "SANCHI STUPA"
    assert result.pages[0].source == "ocr"
