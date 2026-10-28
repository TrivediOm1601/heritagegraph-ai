"""B2 — evidence schemas (canonical contract).

Mirrors ``frontend/src/types/api.ts``: the evidence payload for ONE
selected relationship, including the backend's actual verification status.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItem(BaseModel):
    """One piece of supporting evidence for a relationship."""

    id: str = Field(min_length=1, max_length=128, description="Evidence id.")
    source: str = Field(
        min_length=1, max_length=256, description="Where the evidence comes from (archive, record, transcription, ...)."
    )
    excerpt: Optional[str] = Field(
        default=None, description="Short quotation or description supporting the relationship."
    )
    url: Optional[str] = Field(default=None, description="Optional link to the source material.")


class EvidenceResponse(BaseModel):
    """Payload of GET /api/analysis/{analysis_id}/relationships/{relationship_id}/evidence."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    relationship_id: str = Field(
        alias="relationshipId", min_length=1, max_length=128, description="The relationship this evidence supports."
    )
    verification_status: str = Field(
        default="unverified",
        alias="verificationStatus",
        description=(
            "Backend verification status: one of verified, disputed, "
            "unsupported, unverified. Defaults to 'unverified' — an absent "
            "status is never presented as verified."
        ),
    )
    summary: Optional[str] = Field(default=None, description="Short human summary of the evidence.")
    evidence: list[EvidenceItem] = Field(default_factory=list, description="Supporting evidence items.")
    notes: Optional[list[str]] = Field(default=None, description="Extra uncertainty notes.")
