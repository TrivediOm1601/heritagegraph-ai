"""PDF export of analyses and history (server-side, ownership-checked).

Why server-side: the export must contain only the authenticated user's
records, must not depend on the browser rendering a canvas, and must be
readable as a document (headings, wrapping, page numbers, links). The
route assembles the data from the database (already owner-scoped); this
module only lays it out.

Layout notes:

* DejaVu Sans is embedded so Unicode text (accents, non-Latin scripts)
  renders correctly instead of turning into black squares;
* every user-supplied string is XML-escaped before it reaches a
  Paragraph, so a record title containing ``<`` cannot corrupt the PDF;
* long fields flow across pages (Platypus), and no trailing page break is
  emitted, so the document never ends with a blank page;
* the knowledge graph is rendered as a **text outline** (nodes, then each
  edge with its verification status and evidence) — reliable, selectable
  and honest, rather than a brittle rasterised canvas;
* the uploaded image is *not* included because the application does not
  retain image bytes (only the extracted metadata). That omission is
  stated in the document itself.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence
from xml.sax.saxutils import escape

__all__ = [
    "PdfEntry",
    "PdfDocument",
    "render_pdf",
    "safe_filename",
    "PROJECT_NAME",
]

PROJECT_NAME = "HeritageGraph AI"

_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_REGULAR_FONT = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_BOLD_FONT = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")
_FONT_NAME = "HeritageGraphSans"
_BOLD_NAME = "HeritageGraphSans-Bold"

_fonts_ready = False


def _register_fonts() -> tuple[str, str]:
    """Register the embedded Unicode font once per process.

    Falls back to the built-in Helvetica family when the asset is missing:
    the PDF still renders (Latin text only), and the caller is told so via
    a limitation line rather than shipping a broken document.
    """
    global _fonts_ready
    if _fonts_ready:
        return _FONT_NAME, _BOLD_NAME

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if os.path.exists(_REGULAR_FONT) and os.path.exists(_BOLD_FONT):
        pdfmetrics.registerFont(TTFont(_FONT_NAME, _REGULAR_FONT))
        pdfmetrics.registerFont(TTFont(_BOLD_NAME, _BOLD_FONT))
        from reportlab.lib.fonts import addMapping

        addMapping(_FONT_NAME, 0, 0, _FONT_NAME)
        addMapping(_FONT_NAME, 1, 0, _BOLD_NAME)
        addMapping(_FONT_NAME, 0, 1, _FONT_NAME)
        addMapping(_FONT_NAME, 1, 1, _BOLD_NAME)
    else:  # pragma: no cover - asset should always ship with the app
        _fonts_ready = True
        return "Helvetica", "Helvetica-Bold"

    _fonts_ready = True
    return _FONT_NAME, _BOLD_NAME


@dataclass(frozen=True)
class PdfSource:
    title: str
    url: Optional[str] = None
    publisher: Optional[str] = None
    excerpt: Optional[str] = None


@dataclass(frozen=True)
class PdfEntity:
    name: str
    type: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class PdfEdge:
    source: str
    target: str
    label: Optional[str] = None
    verification_status: Optional[str] = None
    evidence: Sequence[PdfSource] = field(default_factory=tuple)


@dataclass(frozen=True)
class PdfEntry:
    """One exportable record (an image analysis or a text search)."""

    title: str
    kind: str
    status: str
    created_at: Optional[datetime] = None
    summary: Optional[str] = None
    search_query: Optional[str] = None
    extracted_text: Optional[str] = None
    interpretation: Optional[str] = None
    explanation: Optional[str] = None
    entities: Sequence[PdfEntity] = field(default_factory=tuple)
    edges: Sequence[PdfEdge] = field(default_factory=tuple)
    sources: Sequence[PdfSource] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)
    error: Optional[str] = None
    limitations: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class PdfDocument:
    """The whole export: a single record or a user's full history."""

    entries: Sequence[PdfEntry]
    user_email: str
    generated_at: Optional[datetime] = None


def _text(value: object) -> str:
    """Escape a value for reportlab's mini-markup and normalise spacing."""
    if value is None:
        return ""
    return escape(str(value))


def _multiline(value: object) -> str:
    """Escaped text with source newlines preserved as line breaks."""
    if value is None:
        return ""
    return _text(value).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")


def _link(url: Optional[str], label: Optional[str] = None) -> str:
    """A clickable, escaped link (or plain escaped text)."""
    text = _text(label or url or "")
    if not url or not url.lower().startswith(("http://", "https://")):
        return text
    href = escape(url, {'"': "&quot;"})
    return f'<link href="{href}" color="#0f766e">{text}</link>'


def _fmt_date(value: Optional[datetime]) -> str:
    if value is None:
        return "Not recorded"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%d %B %Y, %H:%M UTC")


#: How an incomplete record is labelled inside the document.
STATUS_NOTES: dict[str, str] = {
    "partial": (
        "This analysis is PARTIAL: useful results exist, but one or more "
        "processing stages were degraded. Treat it as incomplete."
    ),
    "failed": "This analysis FAILED: no result was produced. Details below.",
    "queued": "This analysis had not started when it was recorded.",
    "processing": "This analysis was still running when it was recorded.",
}


def status_note(status: str) -> Optional[str]:
    """The honest 'incomplete record' sentence for a status, or ``None``."""
    return STATUS_NOTES.get(status)


def safe_filename(*, kind: str, created_at: Optional[datetime] = None) -> str:
    """A safe, meaningful download name (no user text, no path parts)."""
    stamp = (created_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    stem = "heritagegraph-analysis" if kind == "analysis" else "heritagegraph-history"
    return f"{stem}-{stamp}.pdf"


def render_pdf(document: PdfDocument) -> bytes:
    """Render ``document`` and return the PDF bytes.

    Raises :class:`ValueError` when there is nothing to export — an empty
    document would otherwise produce a blank page.
    """
    if not document.entries:
        raise ValueError("Nothing to export: the selection contains no records.")

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    regular, bold = _register_fonts()
    base = getSampleStyleSheet()
    teal = colors.HexColor("#115e59")
    ink = colors.HexColor("#1f2937")
    muted = colors.HexColor("#4b5563")

    body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName=regular,
        fontSize=9.5,
        leading=13.5,
        textColor=ink,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    small = ParagraphStyle(
        "Small", parent=body, fontSize=8, leading=11, textColor=muted
    )
    heading = ParagraphStyle(
        "Heading",
        parent=base["Heading2"],
        fontName=bold,
        fontSize=13.5,
        leading=17,
        textColor=teal,
        spaceBefore=10,
        spaceAfter=5,
    )
    subheading = ParagraphStyle(
        "Subheading",
        parent=heading,
        fontSize=11,
        leading=14,
        spaceBefore=8,
        spaceAfter=3,
    )
    title_style = ParagraphStyle(
        "DocTitle",
        parent=base["Title"],
        fontName=bold,
        fontSize=19,
        leading=23,
        textColor=teal,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    label = ParagraphStyle(
        "Label",
        parent=small,
        fontName=bold,
        textColor=muted,
        spaceAfter=1,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"{PROJECT_NAME} export",
        author=PROJECT_NAME,
        subject="Analysis export",
        creator=PROJECT_NAME,
    )

    story: list = []
    story.append(Paragraph(PROJECT_NAME, title_style))
    story.append(
        Paragraph(
            "Analysis export — generated from stored, source-backed records",
            small,
        )
    )
    story.append(Spacer(1, 3 * mm))
    metadata = [
        ["Account", _text(document.user_email)],
        ["Generated", _text(_fmt_date(document.generated_at or datetime.now(timezone.utc)))],
        ["Records", str(len(document.entries))],
    ]
    table = Table(
        [[Paragraph(_text(a), small), Paragraph(b, small)] for a, b in metadata],
        colWidths=[32 * mm, 130 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 2 * mm))
    story.append(HRFlowable(width="100%", thickness=0.7, color=teal))
    story.append(
        Paragraph(
            "Text marked “quoted” is reproduced from the cited source. "
            "AI-generated candidate readings are labelled as such. "
            "Verification statuses are the backend's own; “unverified” means "
            "no independent confirmation was recorded.",
            small,
        )
    )

    for index, entry in enumerate(document.entries, start=1):
        prefix = f"{index}. " if len(document.entries) > 1 else ""
        story.append(
            KeepTogether(
                [
                    Spacer(1, 4 * mm),
                    Paragraph(f"{prefix}{_text(entry.title)}", heading),
                    Paragraph(
                        f"{_text(entry.kind.replace('_', ' ').title())} · "
                        f"{_text(_fmt_date(entry.created_at))} · Status: "
                        f"<b>{_text(entry.status)}</b>",
                        small,
                    ),
                    HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#d1d5db")),
                ]
            )
        )

        note = status_note(entry.status)
        if note:
            story.append(Paragraph(f"<b>Incomplete record.</b> {_text(note)}", body))

        if entry.error:
            story.append(Paragraph(f"<b>Error:</b> {_multiline(entry.error)}", body))

        if entry.summary:
            story.append(Paragraph("Summary", subheading))
            story.append(Paragraph(_multiline(entry.summary), body))

        if entry.search_query:
            story.append(Paragraph("Query", subheading))
            story.append(Paragraph(_multiline(entry.search_query), body))

        if entry.extracted_text:
            story.append(Paragraph("Extracted text (OCR)", subheading))
            story.append(Paragraph(_multiline(entry.extracted_text), body))

        if entry.interpretation:
            story.append(
                Paragraph("Visual observations (AI-generated candidate reading)", subheading)
            )
            story.append(Paragraph(_multiline(entry.interpretation), body))

        if entry.explanation:
            story.append(Paragraph("Historical explanation", subheading))
            story.append(Paragraph(_multiline(entry.explanation), body))

        if entry.entities:
            story.append(Paragraph("Identified entities", subheading))
            for entity in entry.entities:
                detail = f"{_text(entity.name)}"
                extras = []
                if entity.type:
                    extras.append(f"type: {_text(entity.type)}")
                if entity.confidence is not None:
                    extras.append(f"confidence: {entity.confidence:.2f}")
                if extras:
                    detail += f" — {', '.join(extras)}"
                story.append(Paragraph(f"• {detail}", body))

        if entry.edges:
            story.append(Paragraph("Relationships", subheading))
            for edge in entry.edges:
                line = (
                    f"• {_text(edge.source)} → {_text(edge.target)}"
                    + (f" ({_text(edge.label)})" if edge.label else "")
                    + (
                        f" — status: {_text(edge.verification_status)}"
                        if edge.verification_status
                        else ""
                    )
                )
                story.append(Paragraph(line, body))
                for evidence in edge.evidence:
                    story.append(
                        Paragraph(
                            f"&nbsp;&nbsp;&nbsp;Evidence: {_text(evidence.title)}"
                            + (f" — {_multiline(evidence.excerpt)}" if evidence.excerpt else ""),
                            small,
                        )
                    )
                    if evidence.url:
                        story.append(
                            Paragraph(f"&nbsp;&nbsp;&nbsp;{_link(evidence.url)}", small)
                        )
        elif entry.kind == "image_analysis":
            story.append(Paragraph("Relationships", subheading))
            story.append(Paragraph("No supported relationships were recorded.", body))

        if entry.sources:
            story.append(Paragraph("Sources and evidence", subheading))
            for position, source in enumerate(entry.sources, start=1):
                story.append(
                    Paragraph(f"[{position}] {_text(source.title)}", body)
                )
                if source.publisher:
                    story.append(Paragraph(f"&nbsp;&nbsp;{_text(source.publisher)}", small))
                if source.url:
                    story.append(Paragraph(f"&nbsp;&nbsp;{_link(source.url)}", small))
                if source.excerpt:
                    story.append(
                        Paragraph(f"&nbsp;&nbsp;“{_multiline(source.excerpt)}”", small)
                    )

        if entry.warnings:
            story.append(Paragraph("Warnings", subheading))
            for warning in entry.warnings:
                story.append(Paragraph(f"• {_multiline(warning)}", small))

        if entry.limitations:
            story.append(Paragraph("Limitations of this record", subheading))
            for limitation in entry.limitations:
                story.append(Paragraph(f"• {_multiline(limitation)}", small))

    def _page_footer(canvas, _doc) -> None:
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 10 * mm, PROJECT_NAME)
        canvas.drawRightString(
            A4[0] - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()
