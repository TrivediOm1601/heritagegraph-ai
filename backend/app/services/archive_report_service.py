"""Research report for one heritage archive (server-side PDF).

The report is assembled from the archive's real rows — inventory, extracted
entities, dated events, source references and limitations. It deliberately
**does not** claim that the extraction is complete or that a candidate is a
verified fact; the "Unverified information and limitations" section states
what the pipeline can and cannot do.

Layout follows the existing history export: embedded DejaVu Sans (Unicode
scripts render instead of black squares), escaped user text, page numbers,
and a graph-like evidence outline instead of a rasterised canvas.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Optional, Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..db.models import Archive, ArchiveFile, TimelineEvent
from .pdf_service import PROJECT_NAME, _multiline, _register_fonts, _text, safe_filename

__all__ = ["render_archive_report", "archive_report_filename"]


def _styles():
    regular, bold = _register_fonts()
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ArchiveTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=22,
            leading=27,
            textColor=colors.HexColor("#134e4a"),
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "ArchiveSubtitle",
            parent=base["Normal"],
            fontName=regular,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#57534e"),
        ),
        "heading": ParagraphStyle(
            "ArchiveHeading",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#0f766e"),
            spaceBefore=12,
            spaceAfter=6,
        ),
        "subheading": ParagraphStyle(
            "ArchiveSubheading",
            parent=base["Heading3"],
            fontName=bold,
            fontSize=10.5,
            leading=14,
            textColor=colors.HexColor("#292524"),
            spaceBefore=8,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "ArchiveBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.5,
            leading=13.5,
            textColor=colors.HexColor("#1c1917"),
        ),
        "small": ParagraphStyle(
            "ArchiveSmall",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#57534e"),
        ),
        "cell": ParagraphStyle(
            "ArchiveCell",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8,
            leading=10.5,
        ),
        "cellbold": ParagraphStyle(
            "ArchiveCellBold",
            parent=base["BodyText"],
            fontName=bold,
            fontSize=8,
            leading=10.5,
        ),
    }
    return styles, regular


def archive_report_filename(archive: Archive) -> str:
    """A safe download name (slug of the archive name + date)."""
    slug = "".join(
        character if character.isalnum() else "-"
        for character in (archive.name or "archive").lower()
    ).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug[:60] or "archive"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"heritagegraph-{slug}-{stamp}.pdf"


def _fmt_date(value: Optional[datetime]) -> str:
    if value is None:
        return "Not recorded"
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%d %B %Y, %H:%M UTC")


def _timeline_label(event: TimelineEvent) -> str:
    if event.year_start is None:
        return event.date_text
    if event.year_end is not None and event.year_end != event.year_start:
        return f"{event.date_text} ({event.year_start} to {event.year_end})"
    return f"{event.date_text} ({event.year_start})"


def render_archive_report(
    archive: Archive,
    *,
    stats: dict,
    files: Sequence[ArchiveFile],
    entities: Sequence[dict],
    timeline: Sequence[TimelineEvent],
    owner_email: str,
) -> bytes:
    """Render the archive report and return the PDF bytes."""
    styles, regular = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{archive.name} — HeritageGraph AI archive report",
        author=PROJECT_NAME,
    )

    story: list = []
    story.append(Paragraph("Heritage Archive Report", styles["title"]))
    story.append(Paragraph(_text(archive.name), styles["heading"]))
    story.append(
        Paragraph(
            f"Prepared for {_text(owner_email)} &nbsp;·&nbsp; {_fmt_date(datetime.now(timezone.utc))}",
            styles["subtitle"],
        )
    )

    if archive.description:
        story.append(Spacer(1, 6))
        story.append(Paragraph(_multiline(archive.description), styles["body"]))
    meta_bits = []
    if archive.region:
        meta_bits.append(f"Region: {archive.region}")
    if archive.tags:
        meta_bits.append("Tags: " + ", ".join(str(tag) for tag in archive.tags))
    if meta_bits:
        story.append(Paragraph(_text(" · ".join(meta_bits)), styles["subtitle"]))

    # 1. Overview ---------------------------------------------------------
    story.append(Paragraph("1. Collection overview", styles["heading"]))
    overview_rows = [
        ("Files", str(stats.get("files", 0))),
        ("Completed / partial / failed", f"{stats.get('completed', 0)} / {stats.get('partial', 0)} / {stats.get('failed', 0)}"),
        ("Pages or sections digitised", str(stats.get("pages", 0))),
        ("Characters extracted", f"{stats.get('characters', 0):,}"),
        ("Entity candidates", str(stats.get("entities", 0))),
        ("Dated events", str(stats.get("timeline_events", 0))),
        ("Possible duplicates", str(stats.get("duplicates", 0))),
        (
            "Language hints",
            ", ".join(
                f"{code} ({count})" for code, count in (stats.get("languages") or {}).items()
            )
            or "None detected",
        ),
    ]
    table = Table(overview_rows, colWidths=[70 * mm, 100 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), regular),
                ("FONTNAME", (1, 0), (1, -1), regular),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#57534e")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#e7e5e4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)

    # 2. Inventory --------------------------------------------------------
    story.append(Paragraph("2. Source documents", styles["heading"]))
    inventory_header = [
        Paragraph("File", styles["cellbold"]),
        Paragraph("Type", styles["cellbold"]),
        Paragraph("Status", styles["cellbold"]),
        Paragraph("Pages", styles["cellbold"]),
        Paragraph("Language", styles["cellbold"]),
    ]
    inventory_rows = [inventory_header]
    for row in files:
        if row.kind == "zip":
            continue
        inventory_rows.append(
            [
                Paragraph(_text(row.original_filename), styles["cell"]),
                Paragraph(_text(row.kind), styles["cell"]),
                Paragraph(_text(row.status), styles["cell"]),
                Paragraph(str(row.page_count or 0), styles["cell"]),
                Paragraph(_text(row.language or "undetermined"), styles["cell"]),
            ]
        )
    if len(inventory_rows) == 1:
        story.append(Paragraph("No files have been uploaded to this archive yet.", styles["body"]))
    else:
        inventory = Table(
            inventory_rows, colWidths=[70 * mm, 22 * mm, 25 * mm, 18 * mm, 35 * mm]
        )
        inventory.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f4")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e7e5e4")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(inventory)

    # 3. Entities ---------------------------------------------------------
    story.append(Paragraph("3. Identified entities", styles["heading"]))
    if entities:
        story.append(
            Paragraph(
                "These are extraction candidates, not verified historical facts. "
                "Each one is linked to the passage it came from.",
                styles["small"],
            )
        )
        story.append(Spacer(1, 4))
        entity_rows = [
            [
                Paragraph("Name", styles["cellbold"]),
                Paragraph("Type", styles["cellbold"]),
                Paragraph("Method", styles["cellbold"]),
                Paragraph("Page", styles["cellbold"]),
            ]
        ]
        for entity in entities[:120]:
            entity_rows.append(
                [
                    Paragraph(_text(entity.get("name")), styles["cell"]),
                    Paragraph(_text(entity.get("type")), styles["cell"]),
                    Paragraph(_text(entity.get("extraction_method")), styles["cell"]),
                    Paragraph(
                        str(entity.get("page_number")) if entity.get("page_number") else "—",
                        styles["cell"],
                    ),
                ]
            )
        table = Table(entity_rows, colWidths=[80 * mm, 30 * mm, 35 * mm, 25 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f4")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e7e5e4")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
    else:
        story.append(
            Paragraph("No entity candidates were extracted from this archive.", styles["body"])
        )

    # 4. Timeline ---------------------------------------------------------
    story.append(Paragraph("4. Historical timeline", styles["heading"]))
    if timeline:
        story.append(
            Paragraph(
                "Dates are reproduced as written in the source; approximate and "
                "century dates are not converted into exact years.",
                styles["small"],
            )
        )
        story.append(Spacer(1, 4))
        timeline_rows = [
            [
                Paragraph("Date", styles["cellbold"]),
                Paragraph("Event / passage", styles["cellbold"]),
            ]
        ]
        for event in timeline[:120]:
            timeline_rows.append(
                [
                    Paragraph(_text(_timeline_label(event)), styles["cell"]),
                    Paragraph(
                        f"{_multiline(event.title)}"
                        + (
                            f"<br/><font size=7 color='#57534e'>Source page "
                            f"{event.source_page}</font>"
                            if event.source_page
                            else ""
                        ),
                        styles["cell"],
                    ),
                ]
            )
        table = Table(timeline_rows, colWidths=[40 * mm, 130 * mm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f4")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e7e5e4")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
    else:
        story.append(
            Paragraph("No dated events were found in the digitised text.", styles["body"])
        )

    # 5. Sources ----------------------------------------------------------
    story.append(Paragraph("5. Source references", styles["heading"]))
    story.append(
        Paragraph(
            "Every passage in this report comes from a file you uploaded to this "
            "archive. The original files are preserved on the server; open the "
            "archive in HeritageGraph AI to read a passage in context.",
            styles["body"],
        )
    )
    for row in files[:80]:
        if row.kind == "zip":
            continue
        source_line = (
            f"• <b>{_text(row.original_filename)}</b> — {row.page_count or 0} "
            f"{_text((row.extra or {}).get('page_label', 'page')).lower()}(s), "
            f"status {_text(row.status)}"
        )
        story.append(Paragraph(source_line, styles["small"]))

    # 6. Limitations ------------------------------------------------------
    story.append(Paragraph("6. Unverified information and limitations", styles["heading"]))
    limitations = [
        "Entity candidates come from a gazetteer match and a capitalisation heuristic; they are not a verified identification of a person, place or monument.",
        "Extracted dates are reproduced from the source wording. Approximate dates, centuries and BCE dates may not be comparable as exact numbers.",
        "Language is a script heuristic (Devanagari, Gujarati, Tamil, …), not a confident language detector.",
        "Search over the archive is keyword search; semantic search requires an embedding provider that is not configured.",
        "Duplicate detection flags possible duplicates for review. It never deletes or merges files, and two photographs of the same monument are not treated as duplicates.",
        "Visual identification of monuments from photographs is not performed by the archive pipeline; OCR only extracts text.",
    ]
    for limitation in limitations:
        story.append(Paragraph(f"• {_text(limitation)}", styles["small"]))

    def _footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#78716c"))
        canvas.drawString(18 * mm, 10 * mm, PROJECT_NAME)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
