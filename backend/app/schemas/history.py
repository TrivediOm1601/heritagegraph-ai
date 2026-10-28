"""History request/response schemas (camelCase on the wire)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .analysis import AnalysisResponse
from .graph import GraphResponse
from .search import HistoricalBriefing, HistoricalMatch

HistoryKind = Literal["text_search", "image_analysis"]


class HistoryItem(BaseModel):
    """One row of the personal analysis history."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(min_length=1, max_length=128)
    kind: HistoryKind
    query: Optional[str] = Field(
        default=None, max_length=512, description="Search text or uploaded file name."
    )
    analysis_id: Optional[str] = Field(default=None, alias="analysisId", max_length=128)
    status: str = Field(description="Lifecycle status snapshot (queued … failed).")
    summary: Optional[str] = Field(default=None, max_length=2000)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class HistoryListResponse(BaseModel):
    """Payload of GET /api/history."""

    items: list[HistoryItem] = Field(default_factory=list)
    total: int = Field(ge=0, description="Total rows owned by this user (not just this page).")
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class SearchSnapshot(BaseModel):
    """Stored result of a text search, replayed without re-running it."""

    query: str = Field(min_length=1, max_length=512)
    results: list[HistoricalMatch] = Field(default_factory=list)
    briefing: Optional[HistoricalBriefing] = None
    notice: Optional[str] = None


class HistoryDetailResponse(BaseModel):
    """Payload of GET /api/history/{id} — everything needed to reopen it."""

    entry: HistoryItem
    analysis: Optional[AnalysisResponse] = Field(
        default=None, description="Full stored analysis result (image analyses)."
    )
    graph: Optional[GraphResponse] = Field(
        default=None, description="Stored knowledge graph (image analyses)."
    )
    search: Optional[SearchSnapshot] = Field(
        default=None, description="Stored search result (text searches)."
    )
    limitations: list[str] = Field(
        default_factory=list,
        description=(
            "Honest notes about what this stored record cannot show (for example "
            "the uploaded image itself is not retained)."
        ),
    )
