"""C3 — grounded historical briefing over retrieved sources.

Retrieval (:func:`app.services.retrieval_service.search_historical_records`)
finds *sources*; this module turns those sources into the historical
**explanation** the search page and analysis page display.

Design rule: **extractive, never generative.** Every entry in
``key_facts`` / ``timeline`` is a sentence (or dated line) taken verbatim
from a retrieved source and carries that source's citation index
(``source_index`` → ``briefing.sources[n-1]``). ``summary`` and
``explanation`` are sentences *selected* from those same sources — no
model is asked to recall or paraphrase history, so the briefing cannot
state a fact that no retrieved source contains, and it cannot invent a
citation. ``method`` records this explicitly (``extractive-quotes``) so
the UI can label the text honestly.

That is also why no LLM is called here: without a configured, verified
synthesis provider an ungrounded paraphrase would be indistinguishable
from a grounded one. When such a provider exists, it can replace
:func:`_compose_summary` while keeping the citation contract — and
``method`` must change with it.

Query shapes (the three the product promises):

* **year** (``1946``) — the year article's ``Events``, ``Births`` and
  ``Deaths`` sections are parsed into a dated timeline and people list,
  so a year query answers "what happened in 1946", not "pages whose
  title contains 1946";
* **named** (``Rani ki Vav``, ``Rani Lakshmibai``) — the article's lead
  paragraphs become the explanation, with dated sentences promoted to a
  timeline;
* **general** — same as named, for queries that are neither a year nor a
  proper name.

When no source text is available (live sources off, or the source page
is unreachable) the briefing degrades to the curated records' own
summaries and says so in ``uncertainty``. It never fabricates a
narrative to fill the gap.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Optional, Sequence

import httpx

from ..integrations.wikipedia import (
    fetch_page_text,
    live_sources_enabled,
)
from ..integrations.wikidata import fetch_subject_classes, is_index_description
from ..integrations.wikimedia_media import MEDIA_THUMB_WIDTH, PageMedia, fetch_page_media
from .retrieval_service import LIVE_SOURCE_NAME
from ..schemas.search import (
    BriefingSource,
    HistoricalBriefing,
    KeyFact,
    RelatedTopic,
    SubjectImage,
    SubjectProfile,
    TimelineEntry,
)
from .retrieval_service import RetrievedRecord
from .synthesis_service import synthesise_briefing

__all__ = [
    "METHOD_EXTRACTIVE",
    "QueryKind",
    "build_briefing",
    "classify_query",
    "parse_sections",
]

#: ``HistoricalBriefing.method`` — text selected verbatim from sources.
METHOD_EXTRACTIVE = "extractive-quotes"

#: Bounds so one briefing stays readable and one page cannot blow up the
#: response (all counts are of *source* lines, never generated text).
MAX_EVENTS = 8
MAX_BIRTHS = 5
MAX_DEATHS = 5
MAX_KEY_FACTS = 5
MAX_TIMELINE = 6
MAX_EXPLANATION_PARAGRAPHS = 3
MAX_SUMMARY_CHARS = 600
MAX_EXPLANATION_CHARS = 2500
MAX_DOCUMENT_CHARS = 60_000

QueryKind = Literal["year", "named", "general"]

_YEAR_RE = re.compile(r"^(?:in\s+|c\.\s*|ad\s+)?(\d{3,4})(?:\s*(?:ad|ce|bc|bce))?$", re.IGNORECASE)
_YEAR_RANGE_RE = re.compile(r"^\d{3,4}\s*[-–—]\s*\d{3,4}$")
_YEAR_IN_TEXT = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")
#: "January 6 – …", "6 January – …", "1946 – …", "January 10: …".
_DATE_PATTERN = (
    r"(?:[A-Z][a-z]+\.?\s+\d{1,2}"          # January 6
    r"|\d{1,2}\s+[A-Z][a-z]+\.?"              # 6 January
    r"|[A-Z][a-z]+"                           # January
    r"|\d{4})"                                # 1946
)
#: "January 6 – …", "January 10: …".
_ENTRY_RE = re.compile(
    r"^(?P<date>" + _DATE_PATTERN + r")(?:\s*[–—−-]\s*|\s*:\s*)(?P<text>\S.*)$"
)
#: A bare date line whose events follow on the next line(s).
_DATE_ONLY_RE = re.compile(r"^(?P<date>" + _DATE_PATTERN + r")\.?$")


def classify_query(query: str) -> QueryKind:
    """How a query is interpreted for retrieval and briefing.

    A bare four-digit number between 1000 and 2100 is a **year**; anything
    containing letters is treated as a **named** query (person/place/
    event); the rest (ranges, short numerals) is **general**.
    """
    cleaned = " ".join((query or "").split())
    if not cleaned:
        return "general"
    match = _YEAR_RE.match(cleaned)
    if match and _is_year(int(match.group(1))):
        return "year"
    if _YEAR_RANGE_RE.match(cleaned):
        return "general"
    return "named" if re.search(r"[A-Za-z]", cleaned) else "general"


#: Years the product treats as historical years. Four digits only, so a
#: short numeral like "123" is not silently read as a year.
_MIN_YEAR = 1000
_MAX_YEAR = 2100


def _is_year(value: int) -> bool:
    return _MIN_YEAR <= value <= _MAX_YEAR


def query_year(query: str) -> Optional[int]:
    """The year a **year** query names, else ``None``."""
    match = _YEAR_RE.match(" ".join((query or "").split()))
    if not match:
        return None
    value = int(match.group(1))
    return value if _is_year(value) else None


# ---------------------------------------------------------------------------
# Source-text parsing (pure functions — unit-testable without I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One heading of a source document and the text under it."""

    level: int
    heading: str
    body: str


def parse_sections(text: str) -> tuple[str, list[Section]]:
    """Split ``explaintext`` output into ``(preamble, sections)``.

    MediaWiki emits headings as ``== Heading ==`` lines. Each section's
    body ends where the next heading of the same or a higher level
    begins; a parent's body is therefore just its own lines (use
    :func:`_section_text` to include subsections).
    """
    preamble: list[str] = []
    sections: list[Section] = []
    heading: Optional[tuple[int, str]] = None
    buffer: list[str] = []

    def flush() -> None:
        if heading is not None:
            sections.append(
                Section(level=heading[0], heading=heading[1], body="\n".join(buffer).strip())
            )

    for line in text.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            flush()
            heading = (len(match.group(1)), match.group(2).strip())
            buffer = []
        elif heading is None:
            preamble.append(line)
        else:
            buffer.append(line)
    flush()
    return "\n".join(preamble).strip(), sections


def _section_text(sections: Sequence[Section], names: Iterable[str]) -> str:
    """Body of the first section whose heading matches, subsections included."""
    wanted = {name.casefold() for name in names}
    for index, section in enumerate(sections):
        if section.heading.casefold() not in wanted:
            continue
        parts = [section.body]
        for following in sections[index + 1 :]:
            if following.level <= section.level:
                break
            parts.append(following.body)
        return "\n".join(part for part in parts if part).strip()
    return ""


def _sentences(paragraph: str) -> list[str]:
    """Split a paragraph into sentences (best effort, order preserved)."""
    cleaned = " ".join(paragraph.split())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“])", cleaned)
    return [part.strip() for part in parts if part.strip()]


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs of a source, whitespace collapsed."""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    return [" ".join(block.split()) for block in blocks]


def _entries(block: str, *, limit: int) -> list[tuple[str, str]]:
    """Parse dated lines (``January 6 – …``) into ``(date, description)``.

    Handles the three layouts MediaWiki uses:

    * ``January 6 – The event.`` (date + dash/colon + event);
    * ``January 10`` on its own line, with the event line(s) below it;
    * a continuation line under a dated entry (appended to that entry).

    A bare date line must start a **new** entry — otherwise the first
    following event is glued onto the previous date, which is what the
    first live ``1946`` run produced.
    """
    entries: list[tuple[str, str]] = []
    pending_date: Optional[str] = None
    for raw in block.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        match = _ENTRY_RE.match(line)
        if match:
            pending_date = match.group("date")
            entries.append((pending_date, match.group("text").strip()))
        else:
            date_only = _DATE_ONLY_RE.match(line)
            if date_only:
                pending_date = date_only.group("date")
                entries.append((pending_date, ""))
            elif pending_date is not None and entries:
                # Continuation of the previous dated entry.
                date, description = entries[-1]
                entries[-1] = (date, f"{description} {line}".strip())
        if len(entries) >= limit:
            break
    # A date with no event text under it is not a fact worth quoting.
    return [(date, text) for date, text in entries[:limit] if text]


def _person_name(description: str) -> str:
    """The person named at the start of a Births/Deaths line."""
    head = description.split(",", 1)[0]
    head = re.split(r"\s*\(", head, maxsplit=1)[0]
    return head.strip() or description.strip()


def _dated_sentences(text: str, *, limit: int) -> list[tuple[str, str]]:
    """Sentences that mention a year, paired with that year.

    Only the year is used as the ``date`` label — the rest of the sentence
    is quoted unchanged, so the entry can always be checked against the
    source.
    """
    found: list[tuple[str, str]] = []
    for sentence in _sentences(text):
        years = _YEAR_IN_TEXT.findall(sentence)
        if not years:
            continue
        found.append((years[0], sentence))
        if len(found) >= limit:
            break
    return found


def _first_sentences(text: str, count: int = 2) -> str:
    sentences = _sentences(text)
    return " ".join(sentences[:count]).strip()


# ---------------------------------------------------------------------------
# Briefing assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Primary:
    """The one article the briefing is about, with its text."""

    record_id: str
    title: str
    url: Optional[str]
    publisher: Optional[str]
    excerpt: Optional[str]
    document: Optional[str]


#: Phrases that identify a MediaWiki index page. Such a page lists topics
#: instead of describing one, so explaining it would answer "Indian
#: independence may refer to: …" — the specific article is used instead.
_DISAMBIGUATION_MARKERS = (
    "may refer to",
    "can refer to",
    "most commonly refers to",
)
#: How many candidate titles are probed for a page before falling back to
#: the stored record text (bounded so one search cannot fan out).
MAX_PAGE_PROBES = 3
#: How many related topics are returned (bounded so the UI stays readable).
MAX_RELATED_TOPICS = 8


def _is_disambiguation(text: str) -> bool:
    """True for an index/disambiguation page (checked in its lead only)."""
    head = " ".join(text[:400].split()).casefold()
    return any(marker in head for marker in _DISAMBIGUATION_MARKERS)


def _pick_primary(query: str, hits: Sequence[RetrievedRecord]) -> RetrievedRecord:
    """Exact-title hit first (an article *about* the query), else best score."""
    normalized = " ".join(query.split()).casefold()
    for hit in hits:
        if hit.record.title.strip().casefold() == normalized:
            return hit
    return hits[0]


async def _resolve_primary(
    query: str,
    hits: Sequence[RetrievedRecord],
    *,
    live: bool,
    client: Optional[httpx.AsyncClient],
    environ: Optional[Mapping[str, str]],
) -> tuple[_Primary, list[str]]:
    """Find the article the briefing should be about, and its full text.

    The **query itself is tried first** (MediaWiki follows redirects), which
    is what fixes spelling differences the search engine ranks badly: a
    search for "Qutub Minar" ranks "Mini Qutub Minar" first, but the page
    titled "Qutub Minar" redirects to the right article ("Qutb Minar").

    Index/disambiguation pages are skipped in favour of the first specific
    article among the candidates, so "Indian independence" is explained by
    "Indian independence movement" rather than by its list page.

    Returns the primary plus any provenance notes for ``uncertainty``.
    Never raises: an unreachable source degrades to the stored record text.
    """
    notes: list[str] = []
    if live:
        tried: set[str] = set()
        candidates = [query, *(hit.record.title for hit in hits[: MAX_PAGE_PROBES - 1])]
        keys: list[str] = []
        for candidate in candidates:
            key = " ".join(candidate.split()).casefold()
            if not key or key in tried:
                continue
            tried.add(key)
            keys.append(candidate)

        # Probe the candidate titles **concurrently**. They are independent
        # reads of the same public API, and doing them in sequence made one
        # slow page cost every following probe its own timeout — the reason
        # a first search could outlive the browser's deadline while the
        # (cache-warmed) repeat succeeded. Order is preserved below, so the
        # choice of primary article is unchanged.
        pages = await asyncio.gather(
            *(fetch_page_text(candidate, client=client, environ=environ) for candidate in keys)
        )
        for page in pages:
            if page is None or not page.text.strip():
                continue
            if _is_disambiguation(page.text):
                if not notes:
                    notes.append(
                        f"“{page.title}” is an index page that lists several "
                        f"topics, so the explanation uses a specific article "
                        f"instead."
                    )
                continue
            return (
                _Primary(
                    record_id=f"wiki:{page.page_id}",
                    title=page.title,
                    url=page.url,
                    publisher=LIVE_SOURCE_NAME,
                    excerpt=_first_sentences(page.text, 2)[:600] or None,
                    document=page.text[:MAX_DOCUMENT_CHARS],
                ),
                notes,
            )

    primary_hit = _pick_primary(query, hits)
    content = (primary_hit.record.content or "").strip()
    return (
        _Primary(
            record_id=primary_hit.record.id,
            title=primary_hit.record.title,
            url=primary_hit.record.url,
            publisher=primary_hit.record.source,
            excerpt=primary_hit.excerpt,
            document=content[:MAX_DOCUMENT_CHARS] or None,
        ),
        notes,
    )


def _source_entries(
    primary: _Primary, hits: Sequence[RetrievedRecord]
) -> tuple[list[BriefingSource], list[str], list[RetrievedRecord]]:
    """Cited sources, primary first, plus the remaining topics.

    The primary always carries citation ``1`` because every quoted fact is
    taken from it; the other retrieval hits follow as context, de-duplicated
    against the primary by title or URL. The third value is those non-primary
    hits, so :func:`_subject_and_topics` can build cards for exactly the
    topics that were actually retrieved.
    """
    primary_key = primary.title.strip().casefold()
    scores = [
        hit.score
        for hit in hits
        if hit.record.title.strip().casefold() == primary_key
        or (primary.url and hit.record.url == primary.url)
    ]

    sources = [
        BriefingSource(
            index=1,
            id=primary.record_id,
            title=primary.title,
            url=primary.url,
            publisher=primary.publisher,
            excerpt=primary.excerpt,
            relevance=max(scores) if scores else None,
        )
    ]
    related: list[str] = []
    topic_hits: list[RetrievedRecord] = []
    for hit in hits:
        if hit.record.title.strip().casefold() == primary_key:
            continue
        if primary.url and hit.record.url == primary.url:
            continue
        sources.append(
            BriefingSource(
                index=len(sources) + 1,
                id=hit.record.id,
                title=hit.record.title,
                url=hit.record.url,
                publisher=hit.record.source,
                excerpt=hit.excerpt,
                relevance=hit.score,
            )
        )
        related.append(hit.record.title)
        topic_hits.append(hit)
    return sources, related, topic_hits


# ---------------------------------------------------------------------------
# Feature support: subject photograph + clickable related topics
# ---------------------------------------------------------------------------


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Whitespace-collapsed source text, trimmed to a contract limit.

    Commons image descriptions are sometimes a full paragraph; the contract
    caps the field, so the value is trimmed (marked with an ellipsis) rather
    than rejected — the image must not be dropped because its caption is
    long. The text itself is still the source's own wording.
    """
    if not value:
        return None
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,;:") + "…"


#: MediaWiki title conventions that mark a navigation page rather than a
#: subject. Used together with the Wikidata/description signals: a
#: "… (disambiguation)" page describes no single topic, so it must never
#: become a card (the PDF's "Modi (disambiguation)" case).
_INDEX_TITLE_SUFFIXES = ("(disambiguation)",)
_INDEX_TITLE_PREFIXES = ("list of ",)


def _is_index_title(title: str) -> bool:
    cleaned = " ".join((title or "").split()).casefold()
    if not cleaned:
        return False
    if cleaned.endswith(_INDEX_TITLE_SUFFIXES):
        return True
    return cleaned.startswith(_INDEX_TITLE_PREFIXES)


#: ``Title (qualifier)`` — MediaWiki's own way of distinguishing a *different*
#: entity that shares a name with another article.
_TITLE_QUALIFIER_RE = re.compile(r"^(?P<base>.+?)\s*\((?P<qualifier>[^()]+)\)$")


def _is_same_name_entity(title: str, subject_title: str) -> bool:
    """True when ``title`` is the subject's name plus a qualifier.

    ``Taj Mahal (musician)`` is a different entity that merely shares the
    subject's name — Wikipedia distinguishes it precisely because it is not
    the monument. Offering it as a *related topic* of the monument is the
    "unrelated entity chosen because the name matches" failure, so it is
    dropped. Only an exact name match is treated this way: a genuinely
    different title (``Taj Mahal Palace Hotel``) is left to retrieval.
    """
    match = _TITLE_QUALIFIER_RE.match(" ".join((title or "").split()))
    if match is None:
        return False
    base = " ".join(match.group("base").split()).casefold()
    subject = " ".join((subject_title or "").split()).casefold()
    return bool(base) and base == subject


def _to_image(media: Optional[PageMedia]) -> Optional[SubjectImage]:
    """The display image of a page, with the provenance the source gave us.

    Only source-supplied URLs are used; a page without a page image returns
    ``None`` so the UI shows a placeholder rather than an unrelated picture.
    """
    if media is None:
        return None
    display_url = media.thumbnail_url or media.original_url
    if not display_url:
        return None
    label = media.image_description or media.description or media.title
    return SubjectImage(
        url=display_url,
        thumbnail_url=media.thumbnail_url,
        original_url=media.original_url,
        alt_text=_clip(f"{media.title}: {label}", 512),
        caption=_clip(media.image_description, 1024),
        credit=_clip(media.credit, 512),
        license=_clip(media.license_name, 256),
        license_url=media.license_url,
        source_page_url=media.article_url,
        file_page_url=media.file_page_url,
    )


async def _subject_and_topics(
    primary: _Primary,
    topic_hits: Sequence[RetrievedRecord],
    *,
    live: bool,
    client: Optional[httpx.AsyncClient],
    environ: Optional[Mapping[str, str]],
) -> tuple[Optional[SubjectProfile], list[RelatedTopic]]:
    """Build the subject profile and the related-topic cards.

    Uses two real source lookups, both bounded and both optional:

    * page media (:mod:`app.integrations.wikimedia_media`) — the subject's
      photograph plus every topic's thumbnail, with license and credit;
    * Wikidata (:mod:`app.integrations.wikidata`) — each topic's canonical
      ``type`` and whether it is a navigation page.

    Retrieval is what decides *which* topics appear; these lookups only add
    identity and imagery to topics that were already retrieved. No image,
    type or description is invented, and every failure degrades to a
    missing field. Only runs in live mode: with live sources off there is
    no network call at all.
    """
    if not live:
        return (
            SubjectProfile(
                name=primary.title,
                source_url=primary.url,
                image=None,
            ),
            [],
        )

    titles = [primary.title, *(hit.record.title for hit in topic_hits)]
    media_list = await fetch_page_media(titles, client=client, environ=environ)
    media_by_title = {item.title.strip().casefold(): item for item in media_list}
    classes = await fetch_subject_classes(
        [item.wikibase_item for item in media_list if item.wikibase_item],
        client=client,
        environ=environ,
    )

    def subject_class(media: Optional[PageMedia]):
        if media is None or not media.wikibase_item:
            return None
        return classes.get(media.wikibase_item.strip().upper())

    primary_media = media_by_title.get(primary.title.strip().casefold())
    primary_class = subject_class(primary_media)
    subject = SubjectProfile(
        name=primary.title,
        type=primary_class.type if primary_class else None,
        description=_clip(primary_media.description if primary_media else None, 512),
        source_url=primary.url or (primary_media.article_url if primary_media else None),
        image=_to_image(primary_media),
    )

    topics: list[RelatedTopic] = []
    for hit in topic_hits:
        if len(topics) >= MAX_RELATED_TOPICS:
            break
        media = media_by_title.get(hit.record.title.strip().casefold())
        classified = subject_class(media)
        # A disambiguation/list page describes no single subject: it is an
        # index, not a topic, and must never become a card. Three independent
        # signals, any of which is enough: the source authority's own
        # instance-of, the source's description text, and MediaWiki's title
        # convention.
        if (
            (classified is not None and classified.is_index)
            or is_index_description(media.description if media else None)
            or _is_index_title(hit.record.title)
            or _is_same_name_entity(hit.record.title, primary.title)
        ):
            continue
        topics.append(
            RelatedTopic(
                id=hit.record.id,
                name=hit.record.title,
                type=classified.type if classified else None,
                description=_clip(media.description if media else None, 512),
                query=hit.record.title,
                source_url=hit.record.url or (media.article_url if media else None),
                relevance=hit.score,
                image=_to_image(media),
            )
        )
    return subject, topics


def _compose_summary(kind: QueryKind, lead_text: Optional[str], events: Sequence[tuple[str, str]]) -> Optional[str]:
    """Summary: selected source sentences (year: the first recorded events)."""
    if kind == "year" and events:
        head = " ".join(f"{date} — {text}" for date, text in events[:2])
        return f"Recorded events include: {head}"[:MAX_SUMMARY_CHARS]
    if lead_text:
        summary = _first_sentences(lead_text, 2)
        return summary[:MAX_SUMMARY_CHARS] or None
    return None


def _compose_explanation(kind: QueryKind, lead_text: Optional[str]) -> Optional[str]:
    """Explanation: the source's own lead paragraphs, unedited.

    ``lead_text`` is the article preamble (text before the first heading)
    — never the whole document, so section headings such as ``== History
    ==`` can never leak into the prose shown to the user.
    """
    if not lead_text:
        return None
    paragraphs = _paragraphs(lead_text)
    if kind == "year":
        # The year article's lead is its calendar description; the dated
        # events below carry the history. Keep the lead short.
        paragraphs = paragraphs[:1]
    selected = paragraphs[:MAX_EXPLANATION_PARAGRAPHS]
    joined = "\n\n".join(selected).strip()
    if len(joined) > MAX_EXPLANATION_CHARS:
        joined = joined[:MAX_EXPLANATION_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return joined or None


def _uncertainty_notes(
    *,
    kind: QueryKind,
    live: bool,
    document: Optional[str],
    sources: Sequence[BriefingSource],
    cited: Sequence[int],
    events: Sequence[tuple[str, str]],
    year: Optional[int],
) -> list[str]:
    notes: list[str] = []
    if not live:
        notes.append(
            "Live historical sources are disabled (HG_LIVE_SOURCES is off): "
            "this briefing uses the curated local record store only."
        )
    if not document:
        notes.append(
            "No source text could be retrieved for this query, so no "
            "historical explanation is shown."
        )
    if len(sources) == 1:
        notes.append(
            "Only one source was retrieved; these statements are not "
            "cross-checked against a second source."
        )
    elif len(cited) <= 1:
        notes.append(
            "The explanation is drawn from a single source (citation 1); "
            "the other retrieved records are listed for context only."
        )
    if kind == "year" and year is not None and not events:
        notes.append(
            f"The retrieved source records no dated events for {year}."
        )
    notes.append(
        "Related topics are the other pages the source retrieved for this "
        "query; they are candidates for exploration, not verified historical "
        "relationships to the subject."
    )
    notes.append(
        "The explanation quotes retrieved source material verbatim; it is a "
        "summary of secondary sources, not independent historical verification."
    )
    return notes


async def build_briefing(
    query: str,
    hits: Sequence[RetrievedRecord],
    *,
    client: Optional[httpx.AsyncClient] = None,
    environ: Optional[Mapping[str, str]] = None,
    extra_uncertainty: Sequence[str] = (),
) -> Optional[HistoricalBriefing]:
    """Assemble a grounded briefing from retrieval hits.

    Returns ``None`` when nothing was retrieved (the caller keeps its empty
    state). Never raises for source problems: an unreachable page simply
    reduces the briefing to the records' stored summaries, with a note in
    ``uncertainty``.
    """
    if not hits:
        return None

    cleaned_query = " ".join((query or "").split())
    kind = classify_query(cleaned_query)
    year = query_year(cleaned_query)
    live = live_sources_enabled(environ)

    primary, primary_notes = await _resolve_primary(
        cleaned_query, hits, live=live, client=client, environ=environ
    )
    sources, related, topic_hits = _source_entries(primary, hits)
    subject, related_topics = await _subject_and_topics(
        primary, topic_hits, live=live, client=client, environ=environ
    )
    document = primary.document

    preamble = ""
    sections: list[Section] = []
    if document:
        preamble, sections = parse_sections(document)

    #: Text the summary/explanation is drawn from: the article's lead, or —
    #: when the lead is a one-liner (list-like pages) — the lead plus the
    #: first section that actually carries sentences.
    lead_text = preamble
    if kind != "year" and document and len(_sentences(preamble)) < 2:
        for section in sections:
            if len(_sentences(section.body)) >= 2:
                lead_text = f"{preamble}\n\n{section.body}"
                break

    events: list[tuple[str, str]] = []
    births: list[tuple[str, str]] = []
    deaths: list[tuple[str, str]] = []
    if kind == "year" and document:
        events = _entries(_section_text(sections, {"events"}), limit=MAX_EVENTS)
        births = _entries(_section_text(sections, {"births"}), limit=MAX_BIRTHS)
        deaths = _entries(_section_text(sections, {"deaths"}), limit=MAX_DEATHS)

    key_facts: list[KeyFact] = []
    timeline: list[TimelineEntry] = []

    if kind == "year" and events:
        timeline = [
            TimelineEntry(
                date=_clip(date, 120) or date, description=_clip(text, 2000) or text,
                source_index=1,
            )
            for date, text in events
        ]
        # Facts for a year are its dated events (already in the timeline);
        # key_facts stays empty rather than duplicating them.
    elif document:
        # Timeline scans the whole document (the dated material usually
        # lives in a body section); facts come from the lead only.
        # A quoted sentence can exceed the contract's field limit (some
        # source paragraphs are one very long sentence). It is trimmed, not
        # dropped — an over-long quote must not cost the reader the whole
        # briefing, which is what an unclipped ValidationError used to do.
        for year_label, sentence in _dated_sentences(document, limit=MAX_TIMELINE):
            description = _clip(sentence, 2000)
            if not description:
                continue
            timeline.append(
                TimelineEntry(date=year_label, description=description, source_index=1)
            )
        for paragraph in _paragraphs(lead_text)[:1]:
            for sentence in _sentences(paragraph):
                key_facts.append(KeyFact(text=sentence[:2000], source_index=1))
                if len(key_facts) >= MAX_KEY_FACTS:
                    break
            break

    # Births and deaths come from two different sections of the source, so
    # each name is labelled: an unlabelled list would read as if both were
    # birth entries for the same year.
    people = [
        *(f"{date} (born): {_person_name(text)}" for date, text in births if _person_name(text)),
        *(f"{date} (died): {_person_name(text)}" for date, text in deaths if _person_name(text)),
    ]

    cited = sorted(
        {fact.source_index for fact in key_facts}
        | {entry.source_index for entry in timeline}
    )

    # Optional generated summary (only when a synthesis provider is
    # configured *and* opted in). It is built from the same quoted
    # statements shown on the page, and is dropped — with a visible note —
    # if it fails the grounding checks in the synthesis service.
    briefing = HistoricalBriefing(
        query=cleaned_query,
        query_kind=kind,
        method=METHOD_EXTRACTIVE,
        summary=_compose_summary(kind, lead_text, events),
        explanation=_compose_explanation(kind, lead_text),
        key_facts=key_facts,
        timeline=timeline,
        people=people,
        related=related,
        related_topics=related_topics,
        subject=subject,
        sources=sources,
        uncertainty=[],
    )
    outcome = await synthesise_briefing(briefing, client=client, environ=environ)
    briefing.synthesis = outcome.synthesis
    synthesis_notes = outcome.notes

    briefing.uncertainty = synthesis_notes + [*extra_uncertainty, *primary_notes] + _uncertainty_notes(
        kind=kind,
        live=live,
        document=document,
        sources=sources,
        cited=cited,
        events=events,
        year=year,
    )
    return briefing
