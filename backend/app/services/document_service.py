"""Document and image digitisation for the Heritage Archive Studio.

One entry point — :func:`extract_document` — turns stored bytes into
page-level text with an honest account of how it was obtained.

Supported formats and what "page" means for each:

====================  ======================================================
PDF                   real page numbers (text layer; scanned pages are
                      rasterised and OCR'd when an OCR provider is set up)
DOCX                  numbered structural sections, split at headings
PPTX                  real slide numbers, plus speaker notes
TXT / Markdown        numbered sections, split at Markdown headings
====================  ======================================================

Design rules that matter for historical material:

* **Never invent text.** A page whose text layer is empty and whose OCR
  found nothing stays empty with ``text_source='none'``; the file is then
  reported as ``partial``/``failed`` rather than "digitised".
* **Never silently replace the original.** Extraction is stored verbatim;
  translation (when added) would be a separate field.
* **Keep the location.** Every page carries its page/slide/section number so
  a search hit can point at the passage it came from.
* **Language is a heuristic.** Script detection identifies the writing
  system; it is labelled ``script-heuristic`` and never presented as a
  confidently detected language.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Iterable, Optional
from xml.etree import ElementTree

from ..core.logging import get_logger
from ..integrations import ProviderConfigurationError, ProviderError
from ..integrations.ocr import run_ocr

logger = get_logger("archive.documents")

__all__ = [
    "DocumentExtraction",
    "ExtractedPage",
    "ExtractionError",
    "extract_document",
    "detect_language",
    "normalise_text",
    "text_fingerprint",
    "supported_kind_for_extension",
    "DOCUMENT_EXTENSIONS",
]

#: Extension → archive-file kind. The ingestion route sniffs the real format
#: as well; this map is the first, cheap classification.
DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".zip": "zip",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".gif": "image",
    ".tif": "image",
    ".tiff": "image",
    ".avif": "image",
}

#: MIME types accepted for archive uploads (extension is still checked).
DOCUMENT_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/x-zip-compressed",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "application/octet-stream",
    }
)

#: A scanned page is only sent to OCR when its text layer is this empty.
_SCANNED_PAGE_CHAR_THRESHOLD = 24
#: OCR is bounded per document: a 300-page scan must not become 300 paid
#: requests inside one upload.
DEFAULT_MAX_OCR_PAGES = 40
#: Rasterisation scale for OCR of scanned pages (≈144 dpi).
_OCR_RENDER_SCALE = 2.0


class ExtractionError(ValueError):
    """The file could not be read as its declared format (safe message)."""


@dataclass(frozen=True)
class ExtractedPage:
    """Text of one page/slide/section and where it came from."""

    page_number: int
    text: str
    #: ``text-layer`` | ``ocr`` | ``extracted`` | ``none``
    source: str = "extracted"


@dataclass(frozen=True)
class DocumentExtraction:
    """Everything the pipeline needs to persist one digitised file."""

    pages: list[ExtractedPage]
    text: str
    page_label: str
    language: Optional[str]
    language_script: Optional[str]
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: True when a stage that was configured did not succeed (→ ``partial``).
    degraded: bool = False

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def page_count(self) -> int:
        return len(self.pages)


def supported_kind_for_extension(filename: str) -> Optional[str]:
    """Archive-file kind implied by ``filename`` (``None`` when unsupported)."""
    extension = pathlib.PurePath(filename or "").suffix.lower()
    return DOCUMENT_EXTENSIONS.get(extension)


def normalise_text(text: str) -> str:
    """Whitespace-collapsed, Unicode-NFKC text (original line breaks kept).

    Deliberately conservative: diacritics, punctuation and non-Latin scripts
    are preserved — they are part of a historical name, not noise.
    """
    if not text:
        return ""
    normalised = unicodedata.normalize("NFKC", text)
    lines = []
    for raw in normalised.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", raw).strip()
        lines.append(line)
    # Collapse runs of blank lines, keep paragraph boundaries.
    collapsed: list[str] = []
    for line in lines:
        if not line and collapsed and not collapsed[-1]:
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()


def text_fingerprint(text: str) -> str:
    """Stable digest of normalised text (near-identical document detection).

    Words only, lowercased: punctuation and whitespace differences between
    two scans of the same document do not make them different documents.
    """
    words = re.findall(r"[\w\u0900-\u0DFF]+", text.casefold())
    return hashlib.sha256(" ".join(words).encode("utf-8")).hexdigest()


#: Script ranges used for language *hints*. Counted, never guessed.
_SCRIPTS: tuple[tuple[str, str, int, int], ...] = (
    ("Devanagari", "hi", 0x0900, 0x097F),
    ("Bengali", "bn", 0x0980, 0x09FF),
    ("Gujarati", "gu", 0x0A80, 0x0AFF),
    ("Tamil", "ta", 0x0B80, 0x0BFF),
    ("Telugu", "te", 0x0C00, 0x0C7F),
    ("Kannada", "kn", 0x0C80, 0x0CFF),
    ("Malayalam", "ml", 0x0D00, 0x0D7F),
    ("Arabic", "ar", 0x0600, 0x06FF),
    ("CJK", "zh", 0x4E00, 0x9FFF),
)


def detect_language(text: str) -> tuple[Optional[str], Optional[str]]:
    """``(language_hint, script_name)`` from the writing system actually used.

    Latin text is reported as ``und-Latn`` (undetermined Latin) rather than
    guessed as English: a colonial-era document can be in any Latin-script
    language, and the UI labels the value as a script heuristic.
    """
    if not text.strip():
        return None, None
    counts: dict[str, int] = {}
    latin = 0
    for character in text:
        code = ord(character)
        if ("a" <= character.lower() <= "z"):
            latin += 1
            continue
        if code < 0x80:
            continue
        for name, code2, start, end in _SCRIPTS:
            if start <= code <= end:
                counts[name] = counts.get(name, 0) + 1
                break
    if counts:
        dominant, _count = max(counts.items(), key=lambda item: item[1])
        iso = next((iso for name, iso, _s, _e in _SCRIPTS if name == dominant), "und")
        # Devanagari is shared by Hindi, Marathi, Nepali and Sanskrit. Report
        # the script honestly instead of pretending to know which language.
        if dominant == "Devanagari":
            return "hi", "Devanagari"
        return iso, dominant
    if latin:
        return "und-Latn", "Latin"
    return "und", None


def _decode_text(data: bytes) -> tuple[str, str]:
    """Decode a plain text file, returning ``(text, encoding_used)``."""
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf_metadata(reader) -> dict:
    """Title/author/subject from the PDF's own metadata (never invented)."""
    try:
        raw = reader.metadata or {}
    except Exception:  # noqa: BLE001 — malformed metadata is not fatal
        return {}
    fields = {}
    for source, target in (
        ("/Title", "title"),
        ("/Author", "author"),
        ("/Subject", "subject"),
        ("/Creator", "creator"),
    ):
        value = raw.get(source) if hasattr(raw, "get") else None
        if isinstance(value, str) and value.strip():
            fields[target] = value.strip()[:300]
    return fields


def _render_pdf_page(data: bytes, index: int) -> Optional[bytes]:
    """Rasterise one PDF page to PNG for OCR (``None`` when unsupported)."""
    try:
        import pypdfium2 as pdfium  # imported lazily: optional at runtime
    except Exception:  # noqa: BLE001 — optional dependency
        return None
    document = None
    try:
        document = pdfium.PdfDocument(data)
        page = document[index]
        bitmap = page.render(scale=_OCR_RENDER_SCALE)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — a broken page must not end the doc
        logger.warning("PDF page %d could not be rendered for OCR: %s", index + 1, type(exc).__name__)
        return None
    finally:
        try:
            if document is not None:
                document.close()
        except Exception:  # noqa: BLE001
            pass


async def _extract_pdf(
    data: bytes,
    *,
    filename: str,
    ocr_client=None,
    max_ocr_pages: int,
) -> DocumentExtraction:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is installed
        raise ExtractionError("PDF support is not installed on the server.") from exc

    try:
        reader = PdfReader(io.BytesIO(data))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError(
                    "This PDF is password-protected, so its text cannot be "
                    "extracted. Upload an unprotected copy."
                ) from exc
        page_total = len(reader.pages)
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(
            "This file could not be read as a PDF (it may be corrupt)."
        ) from exc

    if page_total == 0:
        raise ExtractionError("This PDF has no pages.")

    pages: list[ExtractedPage] = []
    warnings: list[str] = []
    degraded = False
    ocr_attempted = 0
    ocr_unavailable = False
    scanned_pages = 0

    for index in range(page_total):
        try:
            text = normalise_text(reader.pages[index].extract_text() or "")
        except Exception as exc:  # noqa: BLE001 — one broken page is not fatal
            logger.warning("PDF page %d text extraction failed: %s", index + 1, type(exc).__name__)
            text = ""
        source = "text-layer"
        if len(text) < _SCANNED_PAGE_CHAR_THRESHOLD:
            scanned_pages += 1
            # A scanned page: try OCR when a provider is configured and the
            # per-document OCR budget has room.
            if not ocr_unavailable and ocr_attempted < max_ocr_pages:
                png = _render_pdf_page(data, index)
                if png is None and ocr_attempted == 0:
                    warnings.append(
                        "Scanned pages could not be rasterised for OCR on this "
                        "server; only the PDF text layer was used."
                    )
                if png is not None:
                    ocr_attempted += 1
                    try:
                        result = await run_ocr(
                            png,
                            filename=f"{pathlib.PurePath(filename).stem}-p{index + 1}.png",
                            content_type="image/png",
                            client=ocr_client,
                        )
                        recognised = normalise_text(result.text)
                        if recognised:
                            text = recognised
                            source = "ocr"
                    except ProviderConfigurationError as exc:
                        # OCR is optional; report it once, never per page.
                        ocr_unavailable = True
                        warnings.append(f"OCR skipped: {exc}")
                    except ProviderError as exc:
                        degraded = True
                        warnings.append(
                            f"Page {index + 1} could not be OCR'd: {exc}"
                        )
                    except Exception as exc:  # noqa: BLE001 — provider boundary
                        degraded = True
                        logger.warning(
                            "PDF page %d OCR failed: %s", index + 1, type(exc).__name__
                        )
                        warnings.append(
                            f"Page {index + 1} could not be OCR'd by the configured provider."
                        )
            if not text:
                source = "none"
        pages.append(
            ExtractedPage(page_number=index + 1, text=text, source=source)
        )

    if ocr_attempted >= max_ocr_pages and scanned_pages > ocr_attempted:
        warnings.append(
            f"OCR was limited to {max_ocr_pages} scanned pages of this document; "
            "the remaining scanned pages have no recognised text."
        )

    text = "\n\n".join(page.text for page in pages if page.text)
    empty_pages = sum(1 for page in pages if not page.text.strip())
    if empty_pages and empty_pages == page_total:
        warnings.append(
            "No text could be extracted from this PDF: every page is empty or "
            "contains no machine-readable text."
        )

    metadata = _pdf_metadata(reader)
    metadata.update(
        {
            "page_count": page_total,
            "scanned_pages": scanned_pages,
            "ocr_pages": ocr_attempted,
            "empty_pages": empty_pages,
            "page_label": "Page",
        }
    )
    language, script = detect_language(text)
    return DocumentExtraction(
        pages=pages,
        text=text,
        page_label="Page",
        language=language,
        language_script=script,
        metadata=metadata,
        warnings=warnings,
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# DOCX / PPTX (both are ZIP + XML; no third-party parser required)
# ---------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _xml_root(archive: zipfile.ZipFile, name: str) -> Optional[ElementTree.Element]:
    try:
        payload = archive.read(name)
    except KeyError:
        return None
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ExtractionError(
            "This document contains XML that could not be read (it may be corrupt)."
        ) from exc


def _docx_paragraph_text(paragraph: ElementTree.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t" and node.text:
            pieces.append(node.text)
        elif node.tag == f"{_W}tab":
            pieces.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            pieces.append("\n")
    return "".join(pieces)


def _docx_style(paragraph: ElementTree.Element) -> str:
    properties = paragraph.find(f"{_W}pPr")
    if properties is None:
        return ""
    style = properties.find(f"{_W}pStyle")
    if style is None:
        return ""
    return (style.get(f"{_W}val") or "").strip()


def _docx_table_text(table: ElementTree.Element) -> str:
    rows: list[str] = []
    for row in table.findall(f"{_W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{_W}tc"):
            text = " ".join(
                filter(None, (_docx_paragraph_text(p) for p in cell.findall(f"{_W}p")))
            ).strip()
            if text:
                cells.append(text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _is_heading(style: str, text: str) -> bool:
    lowered = style.lower()
    if lowered.startswith("heading") or lowered in {"title", "subtitle"}:
        return True
    # Fallback for documents whose styles are missing: a short ALL-CAPS line
    # is a heading in most historical reports.
    return bool(text) and len(text) < 80 and text.upper() == text and any(c.isalpha() for c in text)


def _extract_docx(data: bytes, *, filename: str) -> DocumentExtraction:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(
            "This file could not be read as a DOCX document (it may be corrupt)."
        ) from exc

    with archive:
        if "word/document.xml" not in archive.namelist():
            raise ExtractionError(
                "This file is a ZIP archive, not a Word document. Upload it as a "
                "ZIP archive instead."
            )
        root = _xml_root(archive, "word/document.xml")
        if root is None:
            raise ExtractionError("This Word document has no readable body.")
        core = _xml_root(archive, "docProps/core.xml")

        body = root.find(f"{_W}body")
        blocks: list[tuple[str, str]] = []  # (kind, text) kind: heading|body
        for child in list(body) if body is not None else []:
            if child.tag == f"{_W}p":
                text = normalise_text(_docx_paragraph_text(child))
                if text:
                    blocks.append(
                        ("heading" if _is_heading(_docx_style(child), text) else "body", text)
                    )
            elif child.tag == f"{_W}tbl":
                table = normalise_text(_docx_table_text(child))
                if table:
                    blocks.append(("table", table))

    sections: list[list[str]] = []
    current: list[str] = []
    for kind, text in blocks:
        if kind == "heading" and current:
            sections.append(current)
            current = [text]
        else:
            current.append(text)
    if current:
        sections.append(current)
    if not sections:
        sections = [[]]

    pages = [
        ExtractedPage(page_number=index + 1, text="\n".join(section), source="extracted")
        for index, section in enumerate(sections)
    ]
    text = "\n\n".join(page.text for page in pages if page.text)
    metadata: dict = {"page_label": "Section", "page_count": len(pages)}
    if core is not None:
        title = core.findtext("{http://purl.org/dc/elements/1.1/}title")
        creator = core.findtext("{http://purl.org/dc/elements/1.1/}creator")
        if title and title.strip():
            metadata["title"] = title.strip()[:300]
        if creator and creator.strip():
            metadata["author"] = creator.strip()[:300]
    warnings: list[str] = []
    if not text.strip():
        warnings.append(
            "No text could be extracted from this Word document: it may contain "
            "only images. Upload those images separately for OCR."
        )
    language, script = detect_language(text)
    return DocumentExtraction(
        pages=pages,
        text=text,
        page_label="Section",
        language=language,
        language_script=script,
        metadata=metadata,
        warnings=warnings,
    )


_SLIDE_PATTERN = re.compile(r"ppt/slides/slide(\d+)\.xml$")
_NOTES_PATTERN = re.compile(r"ppt/notesSlides/notesSlide(\d+)\.xml$")


def _drawing_text(root: ElementTree.Element) -> str:
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_A}p"):
        pieces = [
            node.text
            for node in paragraph.iter(f"{_A}t")
            if node.text
        ]
        line = normalise_text("".join(pieces))
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _extract_pptx(data: bytes, *, filename: str) -> DocumentExtraction:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(
            "This file could not be read as a PPTX presentation (it may be corrupt)."
        ) from exc

    with archive:
        names = archive.namelist()
        slides = sorted(
            (
                (int(match.group(1)), name)
                for name in names
                if (match := _SLIDE_PATTERN.search(name))
            ),
            key=lambda item: item[0],
        )
        if not slides:
            raise ExtractionError(
                "This file is a ZIP archive, not a PowerPoint presentation. "
                "Upload it as a ZIP archive instead."
            )
        notes = {
            int(match.group(1)): name
            for name in names
            if (match := _NOTES_PATTERN.search(name))
        }
        pages: list[ExtractedPage] = []
        for number, name in slides:
            root = _xml_root(archive, name)
            text = _drawing_text(root) if root is not None else ""
            note_name = notes.get(number)
            if note_name:
                note_root = _xml_root(archive, note_name)
                note_text = _drawing_text(note_root) if note_root is not None else ""
                if note_text:
                    text = f"{text}\n\nSpeaker notes:\n{note_text}".strip()
            pages.append(
                ExtractedPage(page_number=number, text=text, source="extracted")
            )
        embedded_images = len([n for n in names if n.startswith("ppt/media/")])
        core = _xml_root(archive, "docProps/core.xml")

    text = "\n\n".join(page.text for page in pages if page.text)
    metadata: dict = {
        "page_label": "Slide",
        "page_count": len(pages),
        "embedded_images": embedded_images,
    }
    if core is not None:
        title = core.findtext("{http://purl.org/dc/elements/1.1/}title")
        creator = core.findtext("{http://purl.org/dc/elements/1.1/}creator")
        if title and title.strip():
            metadata["title"] = title.strip()[:300]
        if creator and creator.strip():
            metadata["author"] = creator.strip()[:300]
    warnings: list[str] = []
    if embedded_images:
        warnings.append(
            f"This presentation contains {embedded_images} embedded image(s); "
            "they are preserved in the original file but are not OCR'd here. "
            "Upload them separately to analyse them."
        )
    if not text.strip():
        warnings.append("No text could be extracted from this presentation.")
    language, script = detect_language(text)
    return DocumentExtraction(
        pages=pages,
        text=text,
        page_label="Slide",
        language=language,
        language_script=script,
        metadata=metadata,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Plain text / Markdown
# ---------------------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _markdown_to_text(markdown: str) -> str:
    lines: list[str] = []
    for raw in markdown.split("\n"):
        line = raw
        image = _MD_IMAGE.search(line)
        if image:
            caption = image.group(1).strip()
            line = line.replace(image.group(0), f"[image: {caption}]" if caption else "[image]")
        line = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", line)
        heading = _MD_HEADING.match(line)
        if heading:
            line = heading.group(2)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = _MD_EMPHASIS.sub("", line)
        line = re.sub(r"^\s*```.*$", "", line)
        lines.append(line)
    return normalise_text("\n".join(lines))


def _extract_text(data: bytes, *, filename: str, markdown: bool) -> DocumentExtraction:
    decoded, encoding = _decode_text(data)

    # Split on the document's own headings *before* stripping the Markdown,
    # so each section keeps the heading it belongs to.
    sections: list[list[str]] = [[]]
    for raw in decoded.split("\n"):
        if markdown and _MD_HEADING.match(raw) and any(
            line.strip() for line in sections[-1]
        ):
            sections.append([])
        sections[-1].append(raw)

    pages = [
        ExtractedPage(
            page_number=index + 1,
            text=(
                _markdown_to_text("\n".join(section))
                if markdown
                else normalise_text("\n".join(section))
            ),
            source="extracted",
        )
        for index, section in enumerate(sections)
    ]
    joined = "\n\n".join(page.text for page in pages if page.text)
    warnings: list[str] = []
    if not joined.strip():
        warnings.append("This text file is empty.")
    language, script = detect_language(joined)
    metadata = {
        "page_label": "Section",
        "page_count": len(pages),
        "encoding": encoding,
        "characters": len(joined),
    }
    return DocumentExtraction(
        pages=pages,
        text=joined,
        page_label="Section",
        language=language,
        language_script=script,
        metadata=metadata,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def extract_document(
    data: bytes,
    *,
    filename: str,
    kind: str,
    ocr_client=None,
    max_ocr_pages: int = DEFAULT_MAX_OCR_PAGES,
) -> DocumentExtraction:
    """Digitise one stored document.

    ``ocr_client`` is the fixture seam for scanned-PDF OCR (an
    ``httpx.AsyncClient`` with a mock transport); omitted, the configured
    provider is called for real.

    Raises :class:`ExtractionError` when the bytes are not the format the
    filename claims — the caller records the file as ``failed``.
    """
    if not data:
        raise ExtractionError("The file is empty.")
    if kind == "pdf":
        return await _extract_pdf(
            data,
            filename=filename,
            ocr_client=ocr_client,
            max_ocr_pages=max_ocr_pages,
        )
    if kind == "docx":
        return _extract_docx(data, filename=filename)
    if kind == "pptx":
        return _extract_pptx(data, filename=filename)
    if kind == "text":
        markdown = pathlib.PurePath(filename).suffix.lower() in {".md", ".markdown"}
        return _extract_text(data, filename=filename, markdown=markdown)
    raise ExtractionError(
        f"Unsupported document type {kind!r}; supported: PDF, DOCX, PPTX, TXT, Markdown."
    )
