"""Research workspace schemas: collections, notes and archive reports."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CollectionTarget = Literal["archive", "file"]


class CollectionCreateRequest(BaseModel):
    """Payload of POST /api/collections."""

    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class CollectionUpdateRequest(BaseModel):
    """Payload of PATCH /api/collections/{id} (omitted fields unchanged)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)


class CollectionItemRef(BaseModel):
    """One archive or file inside a collection."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    type: CollectionTarget
    id: str
    name: str
    added_at: datetime = Field(alias="addedAt")


class NoteItem(BaseModel):
    """One research note."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    body: str
    subject_query: Optional[str] = Field(default=None, alias="subjectQuery")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class CollectionSummary(BaseModel):
    """One collection in a list."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str
    name: str
    description: Optional[str] = None
    item_count: int = Field(default=0, alias="itemCount")
    note_count: int = Field(default=0, alias="noteCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class CollectionListResponse(BaseModel):
    """Payload of GET /api/collections."""

    items: list[CollectionSummary] = Field(default_factory=list)
    total: int = Field(ge=0)


class CollectionDetailResponse(CollectionSummary):
    """Payload of GET /api/collections/{id}."""

    items: list[CollectionItemRef] = Field(default_factory=list)
    notes: list[NoteItem] = Field(default_factory=list)


class CollectionItemRequest(BaseModel):
    """Payload of POST /api/collections/{id}/items."""

    type: CollectionTarget
    id: str = Field(min_length=1, max_length=128)


class NoteCreateRequest(BaseModel):
    """Payload of POST /api/collections/{id}/notes."""

    body: str = Field(min_length=1, max_length=20000)
    subject_query: Optional[str] = Field(default=None, alias="subjectQuery", max_length=512)


class NoteUpdateRequest(BaseModel):
    """Payload of PATCH /api/notes/{id}."""

    body: str = Field(min_length=1, max_length=20000)
