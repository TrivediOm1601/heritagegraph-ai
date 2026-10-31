"""Heritage Archive Studio request/response schemas (camelCase on the wire)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ArchiveFileKind = Literal["image", "pdf", "docx", "pptx", "text", "zip"]

ArchiveFileStatus = Literal[
    "uploaded",
    "queued",
    "processing",
    "ocr_completed",
    "text_extracted",
    "analysed",
    "completed",
    "partial",
    "failed",
]

DuplicateKind = Literal["exact", "similar_image", "similar_text"]


class ArchiveCreateRequest(BaseModel):
    """Payload of POST /api/archives."""

    name: str = Field(min_length=1, max_length=200, description="Archive display name.")
    description: Optional[str] = Field(
        default=None, max_length=4000, description="What this collection contains."
    )
    region: Optional[str] = Field(default=None, max_length=120)
    tags: Optional[list[str]] = Field(
        default=None, max_length=12, description="Up to 12 short free-form tags."
    )


class ArchiveUpdateRequest(BaseModel):
    """Payload of PATCH /api/archives/{id} (omitted fields stay unchanged)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    region: Optional[str] = Field(default=None, max_length=120)
    tags: Optional[list[str]] = Field(default=None, max_length=12)


class ArchiveStats(BaseModel):
    """Real aggregates for one archive (computed from stored rows)."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    files: int = Field(ge=0)
    completed: int = Field(ge=0)
    partial: int = Field(ge=0)
    failed: int = Field(ge=0)
    processing: int = Field(ge=0)
    pages: int = Field(ge=0)
    characters: int = Field(ge=0)
    entities: int = Field(ge=0)
    timeline_events: int = Field(ge=0, alias="timelineEvents")
    duplicates: int = Field(ge=0)
    by_status: dict[str, int] = Field(default_factory=dict, alias="byStatus")
    by_kind: dict[str, int] = Field(default_factory=dict, alias="byKind")
    languages: dict[str, int] = Field(default_factory=dict)


class ArchiveFileItem(BaseModel):
    """One file inside an archive."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    archive_id: str = Field(alias="archiveId")
    parent_file_id: Optional[str] = Field(default=None, alias="parentFileId")
    filename: str
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes")
    kind: ArchiveFileKind
    status: ArchiveFileStatus
    status_detail: Optional[str] = Field(default=None, alias="statusDetail")
    error: Optional[str] = None
    language: Optional[str] = None
    language_script: Optional[str] = Field(default=None, alias="languageScript")
    page_count: int = Field(default=0, alias="pageCount")
    page_label: str = Field(default="Page", alias="pageLabel")
    duplicate_of_id: Optional[str] = Field(default=None, alias="duplicateOfId")
    duplicate_kind: Optional[DuplicateKind] = Field(default=None, alias="duplicateKind")
    warnings: list[str] = Field(default_factory=list)
    archive_path: Optional[str] = Field(
        default=None, alias="archivePath", description="Path inside the uploaded ZIP."
    )
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class ArchiveSummary(BaseModel):
    """One archive in a list."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    name: str
    description: Optional[str] = None
    region: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    stats: ArchiveStats


class ArchiveListResponse(BaseModel):
    """Payload of GET /api/archives."""

    items: list[ArchiveSummary] = Field(default_factory=list)
    total: int = Field(ge=0)


class ArchiveDetailResponse(ArchiveSummary):
    """Payload of GET /api/archives/{id}."""

    files: list[ArchiveFileItem] = Field(default_factory=list)


class RejectedUpload(BaseModel):
    """One file or ZIP member that could not be accepted."""

    filename: str
    reason: str


class ArchiveUploadResponse(BaseModel):
    """Payload of POST /api/archives/{id}/files (202, processing continues)."""

    accepted: list[ArchiveFileItem] = Field(default_factory=list)
    rejected: list[RejectedUpload] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str


class ExtractedPageItem(BaseModel):
    """One page/slide/section of extracted text."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    page_number: int = Field(alias="pageNumber")
    text: str
    char_count: int = Field(alias="charCount")
    text_source: str = Field(alias="textSource")


class FileEntityItem(BaseModel):
    """One entity candidate linked to a file."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    name: str
    type: str
    page_number: Optional[int] = Field(default=None, alias="pageNumber")
    confidence: Optional[float] = None
    extraction_method: str = Field(alias="extractionMethod")
    excerpt: Optional[str] = None


class ArchiveFileDetailResponse(BaseModel):
    """Payload of GET /api/archive-files/{id}."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    file: ArchiveFileItem
    archive_id: str = Field(alias="archiveId")
    archive_name: str = Field(alias="archiveName")
    pages: list[ExtractedPageItem] = Field(default_factory=list)
    entities: list[FileEntityItem] = Field(default_factory=list)
    timeline: list["TimelineEventItem"] = Field(default_factory=list)
    duplicate_of: Optional[ArchiveFileItem] = Field(default=None, alias="duplicateOf")
    parent_file: Optional[ArchiveFileItem] = Field(default=None, alias="parentFile")
    limitations: list[str] = Field(
        default_factory=list,
        description="Honest notes about what this extraction cannot show.",
    )


class TimelineEventItem(BaseModel):
    """One dated event extracted from a document passage."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    title: str
    description: Optional[str] = None
    date_text: str = Field(alias="dateText")
    year_start: Optional[int] = Field(default=None, alias="yearStart")
    year_end: Optional[int] = Field(default=None, alias="yearEnd")
    era: Literal["ce", "bce", "unknown"]
    precision: Literal["exact", "approximate", "century", "range", "unknown"]
    source_page: Optional[int] = Field(default=None, alias="sourcePage")
    evidence: Optional[str] = None
    verification_status: str = Field(alias="verificationStatus")
    archive_id: Optional[str] = Field(default=None, alias="archiveId")
    file_id: Optional[str] = Field(default=None, alias="fileId")
    source_file: Optional[str] = Field(default=None, alias="sourceFile")


class TimelineResponse(BaseModel):
    """Payload of GET /api/archives/{id}/timeline (and the global timeline)."""

    events: list[TimelineEventItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limitations: list[str] = Field(default_factory=list)


class ArchiveSearchResult(BaseModel):
    """One ranked passage from the archive."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    file_id: str = Field(alias="fileId")
    archive_id: str = Field(alias="archiveId")
    archive_name: str = Field(alias="archiveName")
    filename: str
    kind: str
    page_number: int = Field(alias="pageNumber")
    page_label: str = Field(alias="pageLabel")
    excerpt: str
    score: float
    language: Optional[str] = None
    text_source: str = Field(alias="textSource")
    matched_entities: list[dict] = Field(default_factory=list, alias="matchedEntities")


class ArchiveSearchResponse(BaseModel):
    """Payload of GET /api/archive-search and /api/archives/{id}/search."""

    query: str
    mode: Literal["keyword"] = Field(
        description="Retrieval mode actually used; never mislabelled as semantic."
    )
    results: list[ArchiveSearchResult] = Field(default_factory=list)
    total: int = Field(ge=0)
    notice: Optional[str] = None
    truncated: bool = False


class ReproduceResponse(BaseModel):
    """Payload of POST /api/archive-files/{id}/reprocess."""

    file_id: str = Field(alias="fileId")
    status: str
    message: str


class HeritageSourceRef(BaseModel):
    """One source reference attached to a curated site."""

    label: str
    url: str


class HeritageSiteItem(BaseModel):
    """One curated heritage site (map marker + detail)."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    name: str
    alternative_names: list[str] = Field(default_factory=list, alias="alternativeNames")
    region: str
    state: str
    is_union_territory: bool = Field(default=False, alias="isUnionTerritory")
    district: Optional[str] = None
    city: Optional[str] = None
    category: str
    period_group: Optional[str] = Field(default=None, alias="periodGroup")
    period: Optional[str] = None
    architectural_style: Optional[str] = Field(default=None, alias="architecturalStyle")
    unesco_status: bool = Field(default=False, alias="unescoStatus")
    unesco_year: Optional[int] = Field(default=None, alias="unescoYear")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    coordinate_source: Optional[str] = Field(default=None, alias="coordinateSource")
    coordinate_accuracy: Literal["verified", "approximate"] = Field(
        alias="coordinateAccuracy"
    )
    verification_status: str = Field(alias="verificationStatus")
    description: Optional[str] = None
    source_url: Optional[str] = Field(default=None, alias="sourceUrl")
    source_refs: list["HeritageSourceRef"] = Field(
        default_factory=list, alias="sourceRefs"
    )
    image_url: Optional[str] = Field(default=None, alias="imageUrl")
    image_credit: Optional[str] = Field(default=None, alias="imageCredit")
    image_license: Optional[str] = Field(default=None, alias="imageLicense")
    dataset: str
    story_count: int = Field(default=0, alias="storyCount", ge=0)
    story_categories: list[str] = Field(default_factory=list, alias="storyCategories")
    featured_stories: list["HeritageFeaturedStory"] = Field(
        default_factory=list, alias="featuredStories"
    )


class HeritageFeaturedStory(BaseModel):
    """A headline story shown in the map's site details."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    title: str
    category: str
    category_label: str = Field(alias="categoryLabel")
    narrative_type: str = Field(alias="narrativeType")
    narrative_type_label: str = Field(alias="narrativeTypeLabel")
    is_community: bool = Field(default=False, alias="isCommunity")


class HeritageStateFacet(BaseModel):
    """One state/UT filter option with its real record count."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    name: str
    region: str
    is_union_territory: bool = Field(alias="isUnionTerritory")
    count: int = Field(ge=0)


class HeritageRegionFacet(BaseModel):
    """One macro-region filter option with its real record count."""

    name: str
    count: int = Field(ge=0)


class HeritageCountFacet(BaseModel):
    """A category or historical-period option with its real record count."""

    name: str
    count: int = Field(ge=0)


class HeritageStoryFacet(BaseModel):
    """A story-category filter with the number of *sites* that have one."""

    name: str
    label: str
    count: int = Field(ge=0)


class HeritageFacets(BaseModel):
    """Filter options for GET /api/heritage/sites."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    states: list[HeritageStateFacet] = Field(default_factory=list)
    regions: list[HeritageRegionFacet] = Field(default_factory=list)
    categories: list[HeritageCountFacet] = Field(default_factory=list)
    periods: list[HeritageCountFacet] = Field(default_factory=list)
    story_categories: list[HeritageStoryFacet] = Field(
        default_factory=list, alias="storyCategories"
    )


class HeritageSummary(BaseModel):
    """National counts shown above the map — computed from records."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    total_sites: int = Field(alias="totalSites", ge=0)
    states_represented: int = Field(alias="statesRepresented", ge=0)
    union_territories_represented: int = Field(
        alias="unionTerritoriesRepresented", ge=0
    )
    categories: int = Field(ge=0)
    verified_records: int = Field(alias="verifiedRecords", ge=0)
    approximate_coordinates: int = Field(alias="approximateCoordinates", ge=0)
    unesco_sites: int = Field(alias="unescoSites", ge=0)
    stories: int = Field(default=0, ge=0)
    sites_with_stories: int = Field(default=0, alias="sitesWithStories", ge=0)


class HeritageSitesResponse(BaseModel):
    """Payload of GET /api/heritage/sites (filtered page + national metadata)."""

    sites: list[HeritageSiteItem] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    dataset: str
    notice: str
    summary: HeritageSummary
    facets: HeritageFacets


class HeritageRouteRequest(BaseModel):
    """Payload of POST /api/heritage/route."""

    site_ids: list[str] = Field(alias="siteIds", min_length=2, max_length=12)
    start_site_id: Optional[str] = Field(default=None, alias="startSiteId")
    #: "selection" keeps the user's order; "distance" uses nearest-neighbour.
    order: Literal["selection", "distance"] = "distance"


class HeritageRouteLeg(BaseModel):
    """One straight-line leg between two stops."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    source: str = Field(alias="from")
    target: str = Field(alias="to")
    distance_km: float = Field(alias="distanceKm")


class HeritageRouteResponse(BaseModel):
    """Payload of POST /api/heritage/route (approximate, never a road route)."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    stops: list[HeritageSiteItem] = Field(default_factory=list)
    legs: list[HeritageRouteLeg] = Field(default_factory=list)
    total_distance_km: float = Field(alias="totalDistanceKm")
    distance_method: str = Field(alias="distanceMethod")
    ordering: Literal["selection", "distance"] = "distance"
    notice: str
    excluded_site_ids: list[str] = Field(default_factory=list, alias="excludedSiteIds")


# Resolve the forward reference used by ArchiveFileDetailResponse and the
# featured-story list on heritage sites.
ArchiveFileDetailResponse.model_rebuild()
HeritageSiteItem.model_rebuild()
