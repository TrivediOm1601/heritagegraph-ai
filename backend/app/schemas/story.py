"""Stories & Legends — contract for heritage-site narratives.

The story layer exists to keep *different kinds of knowledge apart*. A
temple's construction record, a Puranic narrative about its deity, a local
legend and a scholarly reading of its carvings are all worth keeping, but
they are not the same kind of claim. Every story therefore carries:

* a ``category`` (what the story is about — history, mythology, ...);
* a ``narrative_type`` (the epistemic basis — documented, religious
  tradition, folklore, scholarly interpretation, ...);
* a ``verification_status`` (the record's own review state);
* explicit sources, and separate ``versions`` when traditions disagree.

No numeric "truth score" exists: a religious tradition is not a failed
archaeological claim, and a documented event is not automatically a better
story. The vocabulary below is the single source of truth shared by the
database, the API and the seed validator.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Story categories, in display order.
STORY_CATEGORIES: tuple[str, ...] = (
    "history",
    "mythology",
    "religious-tradition",
    "local-legend",
    "architecture",
    "culture",
    "lesser-known",
    "oral-history",
)

#: Human labels used by both the API notice and the UI.
STORY_CATEGORY_LABELS: dict[str, str] = {
    "history": "History",
    "mythology": "Mythology",
    "religious-tradition": "Religious traditions",
    "local-legend": "Local legends",
    "architecture": "Architecture",
    "culture": "Culture",
    "lesser-known": "Lesser-known stories",
    "oral-history": "Oral histories",
}

#: Epistemic basis of a narrative. These describe *what kind of account*
#: this is, never how "true" it is.
NARRATIVE_TYPES: tuple[str, ...] = (
    "historically-documented",
    "archaeological-support",
    "religious-text",
    "religious-tradition",
    "mythological-narrative",
    "local-folklore",
    "oral-history",
    "scholarly-interpretation",
    "unverified-account",
    "conflicting-accounts",
    "community-account",
)

NARRATIVE_TYPE_LABELS: dict[str, str] = {
    "historically-documented": "Historically documented",
    "archaeological-support": "Supported by archaeological evidence",
    "religious-text": "Recorded in a religious text",
    "religious-tradition": "Religious tradition",
    "mythological-narrative": "Mythological narrative",
    "local-folklore": "Local folklore",
    "oral-history": "Oral-history account",
    "scholarly-interpretation": "Scholarly interpretation",
    "unverified-account": "Unverified account",
    "conflicting-accounts": "Conflicting accounts",
    "community-account": "Community contribution",
}

#: Source types shown in the source panel.
SOURCE_TYPES: tuple[str, ...] = (
    "historical-record",
    "religious-text",
    "archaeological-publication",
    "government-publication",
    "academic-research",
    "oral-history-interview",
    "community-contribution",
    "secondary-reference",
    "world-heritage-listing",
)

SOURCE_TYPE_LABELS: dict[str, str] = {
    "historical-record": "Historical record",
    "religious-text": "Religious text",
    "archaeological-publication": "Archaeological publication",
    "government-publication": "Government publication",
    "academic-research": "Academic research",
    "oral-history-interview": "Oral-history interview",
    "community-contribution": "Community contribution",
    "secondary-reference": "Secondary reference",
    "world-heritage-listing": "World Heritage listing",
}

#: Languages the UI can display and a contribution may declare. The list is
#: a tag set, not a claim of translated content: the curated seed is English.
STORY_LANGUAGES: tuple[str, ...] = (
    "en",
    "hi",
    "gu",
    "sa",
    "bn",
    "mr",
    "ta",
    "te",
    "kn",
    "ml",
    "or",
    "pa",
    "as",
    "ur",
)

STORY_LANGUAGE_LABELS: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "gu": "Gujarati",
    "sa": "Sanskrit",
    "bn": "Bengali",
    "mr": "Marathi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "or": "Odia",
    "pa": "Punjabi",
    "as": "Assamese",
    "ur": "Urdu",
}

#: Moderation lifecycle for community submissions.
STORY_STATUSES: tuple[str, ...] = ("published", "pending", "rejected")

#: Review state of a story *record* (never a truth score for its narrative).
#: ``source-checked`` means the compiler opened the cited source and the
#: summary matches it; ``community-approved`` only means a moderator allowed
#: the contribution to be shown — not that its account is historically proven.
STORY_VERIFICATION_STATUSES: tuple[str, ...] = (
    "source-checked",
    "unverified",
    "pending-review",
    "community-approved",
)

STORY_NOTICE = (
    "Stories are presented as separate traditions, not merged into one "
    "account. Labels such as 'mythological narrative' or 'local folklore' "
    "describe the basis of the account; they do not rank it against "
    "documented history. A published community contribution is not a "
    "verified historical claim."
)


class StorySourceItem(BaseModel):
    """One traceable source behind a story or one of its versions."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    title: str
    author: Optional[str] = None
    source_type: str = Field(alias="sourceType")
    published: Optional[str] = None
    url: Optional[str] = None
    excerpt: Optional[str] = None
    locator: Optional[str] = None
    licence: Optional[str] = None


class StoryVersionItem(BaseModel):
    """One tradition's account of the same story."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    label: str
    summary: str
    narrative: Optional[str] = None
    tradition: Optional[str] = None
    language: str
    community: Optional[str] = None
    differences: Optional[str] = None
    verification_status: str = Field(alias="verificationStatus")
    sources: list[StorySourceItem] = Field(default_factory=list)


class StorySummary(BaseModel):
    """A story card: enough to choose a story, not the whole narrative."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    site_id: str = Field(alias="siteId")
    title: str
    summary: str
    category: str
    category_label: str = Field(alias="categoryLabel")
    narrative_type: str = Field(alias="narrativeType")
    narrative_type_label: str = Field(alias="narrativeTypeLabel")
    tradition: Optional[str] = None
    language: str
    language_label: str = Field(alias="languageLabel")
    community: Optional[str] = None
    period: Optional[str] = None
    verification_status: str = Field(alias="verificationStatus")
    provenance: str
    is_community: bool = Field(alias="isCommunity")
    version_count: int = Field(alias="versionCount", ge=0)
    source_count: int = Field(alias="sourceCount", ge=0)


class StoryDetail(StorySummary):
    """The full reading view, with sources and alternatives."""

    site_name: str = Field(alias="siteName")
    site_state: Optional[str] = Field(default=None, alias="siteState")
    narrative: str
    cultural_background: Optional[str] = Field(default=None, alias="culturalBackground")
    related_entities: list[str] = Field(default_factory=list, alias="relatedEntities")
    related_sites: list[str] = Field(default_factory=list, alias="relatedSites")
    sources: list[StorySourceItem] = Field(default_factory=list)
    versions: list[StoryVersionItem] = Field(default_factory=list)
    related_stories: list[StorySummary] = Field(
        default_factory=list, alias="relatedStories"
    )


class StoryCategoryFacet(BaseModel):
    """A story-category filter option with its real published count."""

    name: str
    label: str
    count: int = Field(ge=0)


class SiteStoriesResponse(BaseModel):
    """Payload of GET /api/heritage/sites/{siteId}/stories."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    site_id: str = Field(alias="siteId")
    site_name: str = Field(alias="siteName")
    total: int = Field(ge=0)
    stories: list[StorySummary] = Field(default_factory=list)
    categories: list[StoryCategoryFacet] = Field(default_factory=list)
    notice: str = STORY_NOTICE


class StoryAskRequest(BaseModel):
    """A grounded question about one place's stories."""

    model_config = ConfigDict(populate_by_name=True)

    site_id: str = Field(alias="siteId")
    question: str = Field(min_length=3, max_length=400)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class StoryAskResponse(BaseModel):
    """Retrieval-grounded answer. Never generated without matching records."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    answer: str
    matched_stories: list[StorySummary] = Field(
        default_factory=list, alias="matchedStories"
    )
    citations: list[str] = Field(default_factory=list)
    limitation: Optional[str] = None


class StorySubmissionRequest(BaseModel):
    """A community oral-history submission (always starts ``pending``)."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(min_length=3, max_length=200)
    narrative: str = Field(min_length=20, max_length=8000)
    language: str
    community: Optional[str] = Field(default=None, max_length=200)
    tradition: Optional[str] = Field(default=None, max_length=200)
    source_note: Optional[str] = Field(default=None, alias="sourceNote", max_length=2000)
    interview_date: Optional[str] = Field(default=None, alias="interviewDate", max_length=40)
    consent: bool

    @field_validator("title", "narrative")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if value not in STORY_LANGUAGES:
            raise ValueError(f"language must be one of {STORY_LANGUAGES}")
        return value


class StoryModerationRequest(BaseModel):
    """Approve (publish) or reject a pending community story."""

    decision: Literal["approve", "reject"]
    note: Optional[str] = Field(default=None, max_length=1000)


class StorySubmissionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    story: StorySummary
    status: str
    message: str
