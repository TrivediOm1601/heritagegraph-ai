"""B2 — historical search schemas.

Also the home of :class:`HistoricalMatch`, which the contract uses both
for analysis results (``AnalysisResponse.matches``) and for search hits,
exactly like the shared frontend type.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Wire naming for the briefing payload: camelCase, matching the rest of the
#: frozen contract (``extractedText``, ``analysisId``). Python field names
#: stay PEP8; ``populate_by_name`` keeps both spellings readable.

#: Minimum query length, mirroring the browser guard
#: (``MIN_SEARCH_QUERY_LENGTH`` in ``frontend/src/services/api.ts``), so
#: backend and frontend reject the same input.
MIN_QUERY_LENGTH = 2

#: Upper bound to keep a search request reasonable (contract addition,
#: documented in OpenAPI).
MAX_QUERY_LENGTH = 200


class HistoricalMatch(BaseModel):
    """A historical record matched to an extraction, or returned by search."""

    id: str = Field(min_length=1, max_length=128, description="Record id.")
    title: str = Field(min_length=1, max_length=512, description="Display title of the record.")
    year: Optional[str] = Field(
        default=None, max_length=64, description='Free-text year, e.g. "c. 1885".'
    )
    score: Optional[float] = Field(
        default=None, ge=0, le=1, description="Optional 0..1 match strength."
    )
    source: Optional[str] = Field(
        default=None, max_length=256, description="Archive or collection the record comes from."
    )
    url: Optional[str] = Field(
        default=None,
        max_length=1024,
        description=(
            "Canonical link to the cited source material, when the record "
            "has one. Null when no URL exists — never invented."
        ),
    )
    excerpt: Optional[str] = Field(
        default=None,
        max_length=2000,
        description=(
            "A window of the record's own text around the match. Quoted from "
            "the record — never generated."
        ),
    )
    relevance: Optional[Literal["high", "medium", "low"]] = Field(
        default=None,
        description=(
            "Coarse label for `score`, so the UI need not present a precise "
            "percentage: score >= 0.75 high, >= 0.45 medium, otherwise low."
        ),
    )


class BriefingSource(BaseModel):
    """One source cited by a briefing (index 1..N, referenced by facts)."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    index: int = Field(ge=1, description="Citation number used by facts/timeline.")
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    url: Optional[str] = Field(default=None, max_length=1024)
    publisher: Optional[str] = Field(default=None, max_length=256)
    excerpt: Optional[str] = Field(default=None, max_length=4000)
    relevance: Optional[float] = Field(default=None, ge=0, le=1)


class KeyFact(BaseModel):
    """One sentence quoted (verbatim) from the cited source."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    text: str = Field(min_length=1, max_length=2000)
    source_index: int = Field(
        alias="sourceIndex", ge=1, description="Index into `sources`."
    )


class TimelineEntry(BaseModel):
    """A dated event exactly as the cited source records it."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    date: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    source_index: int = Field(
        alias="sourceIndex", ge=1, description="Index into `sources`."
    )


class SubjectImage(BaseModel):
    """A photograph of the subject, with the provenance that carries it.

    Every field is source-supplied (MediaWiki ``pageimages`` / Commons
    ``extmetadata``); a field the source did not provide is ``null`` and the
    UI shows a placeholder instead of a guess. `altText` is built from the
    source's own description/caption so the image is never unlabelled.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    url: Optional[str] = Field(
        default=None, max_length=1024, description="Best display URL (scaled render)."
    )
    thumbnail_url: Optional[str] = Field(default=None, max_length=1024, alias="thumbnailUrl")
    original_url: Optional[str] = Field(default=None, max_length=1024, alias="originalUrl")
    alt_text: Optional[str] = Field(
        default=None, max_length=512, alias="altText", description="Accessible label from the source."
    )
    caption: Optional[str] = Field(
        default=None, max_length=1024, description="Image description from the file page."
    )
    credit: Optional[str] = Field(
        default=None, max_length=512, description="Author/attribution exactly as the source records it."
    )
    license: Optional[str] = Field(
        default=None, max_length=256, description='License short name, e.g. "CC BY-SA 4.0".'
    )
    license_url: Optional[str] = Field(default=None, max_length=1024, alias="licenseUrl")
    #: The subject's page — where the image is published.
    source_page_url: Optional[str] = Field(default=None, max_length=1024, alias="sourcePageUrl")
    #: The file's description page — the attribution link a reader needs.
    file_page_url: Optional[str] = Field(default=None, max_length=1024, alias="filePageUrl")


class SubjectProfile(BaseModel):
    """Who/what the query is about, as the sources describe it."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    name: str = Field(min_length=1, max_length=512, description="Resolved subject title.")
    type: Optional[str] = Field(
        default=None,
        description=(
            "Canonical entity type (person/place/event/date/other) from the "
            "source authority, or null when it is not mapped — never guessed."
        ),
    )
    description: Optional[str] = Field(
        default=None, max_length=512, description="The source's own one-line description."
    )
    source_url: Optional[str] = Field(default=None, max_length=1024, alias="sourceUrl")
    image: Optional[SubjectImage] = Field(
        default=None, description="Representative photograph of this subject, when one exists."
    )


class RelatedTopic(BaseModel):
    """A related subject the user can open — a real identity, not a label."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(
        min_length=1,
        max_length=128,
        description="Stable identity of the topic (source record id, e.g. ``wiki:799638``).",
    )
    name: str = Field(min_length=1, max_length=512, description="Topic title as the source titles it.")
    type: Optional[str] = Field(default=None, description="Canonical entity type, when known.")
    description: Optional[str] = Field(default=None, max_length=512)
    query: str = Field(
        min_length=1,
        max_length=512,
        description="Exact query that opens this topic (its source title).",
    )
    source_url: Optional[str] = Field(default=None, max_length=1024, alias="sourceUrl")
    relevance: Optional[float] = Field(default=None, ge=0, le=1)
    image: Optional[SubjectImage] = None


class BriefingSynthesis(BaseModel):
    """A model-written paragraph over the briefing's quoted statements.

    Deliberately a *separate* field from ``summary``/``explanation``: those
    two are always the sources' own wording, while this one is generated —
    and only ever over the statements the page already shows, with every
    sentence citing them. A reader can therefore tell which text is quoted
    and which is composed.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    method: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    source_indexes: list[int] = Field(
        default_factory=list,
        alias="sourceIndexes",
        description="Citation indexes the summary actually used (into `sources`).",
    )


class HistoricalBriefing(BaseModel):
    """A grounded historical briefing assembled from retrieved sources.

    Every `key_facts`/`timeline` entry is verbatim source text with a
    citation index; `summary` and `explanation` are extractive (chosen
    sentences from those sources), never a model's recollection. The
    `method` field states exactly how the text was produced so the UI can
    label it honestly.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    query: str = Field(min_length=1, max_length=512)
    query_kind: Literal["year", "named", "general"] = Field(
        alias="queryKind",
        description="How the query was interpreted for retrieval.",
    )
    method: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "How the text was produced: 'extractive-quotes' = sentences "
            "selected verbatim from retrieved sources, no generative model."
        ),
    )
    summary: Optional[str] = Field(default=None, max_length=4000)
    explanation: Optional[str] = Field(default=None, max_length=8000)
    key_facts: list[KeyFact] = Field(default_factory=list, alias="keyFacts")
    timeline: list[TimelineEntry] = Field(default_factory=list)
    #: Names taken from the source's own Births/Deaths section (year queries).
    people: list[str] = Field(default_factory=list)
    #: Other retrieved topics that are related to the query (their titles).
    related: list[str] = Field(default_factory=list)
    #: The same topics with their identity, type, description and thumbnail,
    #: so the UI can render clickable cards instead of plain labels. Index
    #: pages (disambiguation/list pages) are excluded: they are not subjects.
    related_topics: list[RelatedTopic] = Field(default_factory=list, alias="relatedTopics")
    #: What the query is about, with its photograph when the source has one.
    subject: Optional[SubjectProfile] = Field(default=None)
    #: Generated summary, present only when a synthesis provider was
    #: configured *and* its answer passed the grounding checks. Absent
    #: means "no generated text" — never "generated text withheld".
    synthesis: Optional[BriefingSynthesis] = Field(default=None)
    sources: list[BriefingSource] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Payload of GET /api/search."""

    query: str = Field(
        min_length=1, description="The normalised query the backend actually ran."
    )
    results: list[HistoricalMatch] = Field(
        default_factory=list,
        description=(
            "Matching historical records. An empty list means 'nothing "
            "matched'; a failed search is never an empty list — it is an "
            "error response."
        ),
    )
    briefing: Optional[HistoricalBriefing] = Field(
        default=None,
        description=(
            "Grounded historical explanation built from the retrieved "
            "sources. Omitted when nothing was retrieved."
        ),
    )
    notice: Optional[str] = Field(
        default=None,
        max_length=512,
        description=(
            "Present when the answer may be incomplete for a reason other "
            "than 'nothing matched' — currently: the live historical source "
            "could not be reached or rate-limited the request. Distinguishes "
            "an empty/thin result from a source outage without failing the "
            "search."
        ),
    )
