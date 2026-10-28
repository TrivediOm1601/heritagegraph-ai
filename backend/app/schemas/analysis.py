"""B2 — analysis request/response schemas (canonical contract).

Request side: the multipart upload contract (field name, accepted types,
size limit) lives here so every member references ONE definition.
Response side: mirrors ``frontend/src/types/api.ts`` exactly — wire keys
are camelCase through aliases while the Python field names stay PEP8.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .entity import AnalysisEntity
from .search import HistoricalBriefing, HistoricalMatch

#: Lifecycle states of an analysis (frontend: ``AnalysisState``).
#:
#: * ``queued`` / ``processing`` — work is pending or running;
#: * ``completed`` — every required processing stage succeeded;
#: * ``partial`` — useful results exist, but a non-critical stage failed
#:   (for example the vision model returned unusable output while OCR
#:   still produced text, or a configured OCR channel errored);
#: * ``failed`` — the required analysis could not be completed.
#:
#: A run is never reported ``completed`` merely because the HTTP request
#: succeeded: the status is derived from the stages' real outcomes.
AnalysisState = Literal["queued", "processing", "completed", "partial", "failed"]

#: States returned by a successful submission (frontend: ``SubmitAnalysisSuccess``).
SubmitState = Literal["queued", "processing"]

# ---------------------------------------------------------------------------
# Multipart request contract (mirrors the frontend's provisional constants
# in frontend/src/services/api.ts: ANALYSIS_FILE_FIELD, ACCEPTED_IMAGE_TYPES,
# MAX_IMAGE_BYTES).
# ---------------------------------------------------------------------------

#: Multipart form field name carrying the image binary.
ANALYSIS_FILE_FIELD = "file"

#: Accepted image MIME types.
ACCEPTED_IMAGE_TYPES: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/tiff",
    "image/avif",
)

#: Accepted filename extensions (fallback when only a generic MIME is sent).
ACCEPTED_IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".tif",
    ".tiff",
    ".avif",
)

#: MIME browsers send when the real type is unknown; only trusted together
#: with an accepted extension (same rule as the browser-side guard).
GENERIC_MEDIA_TYPE = "application/octet-stream"

#: Maximum accepted upload size: 10 MiB, mirroring the frontend guard.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class SubmitAnalysisResponse(BaseModel):
    """Success envelope of POST /api/analysis."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    analysis_id: str = Field(
        alias="analysisId",
        min_length=1,
        max_length=128,
        description="Id to poll via GET /api/analysis/{analysis_id}.",
    )
    status: SubmitState = Field(description="Initial lifecycle state of the queued analysis.")


class AnalysisResponse(BaseModel):
    """Full payload of GET /api/analysis/{analysis_id}."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str = Field(min_length=1, max_length=128, description="The analysis id.")
    status: AnalysisState = Field(description="Current lifecycle state.")
    progress: Optional[float] = Field(
        default=None, ge=0, le=1, description="Progress through processing, 0..1 when reported."
    )
    extracted_text: Optional[str] = Field(
        default=None, alias="extractedText", description="Text extracted from the image (OCR)."
    )
    interpretation: Optional[str] = Field(
        default=None,
        description=(
            "AI-generated reading of the image: candidate identification(s), "
            "visual clues, alternatives and uncertainty. A candidate, never a "
            "verified historical claim."
        ),
    )
    entities: Optional[list[AnalysisEntity]] = Field(
        default=None, description="Detected entities; empty or omitted when none were found."
    )
    matches: Optional[list[HistoricalMatch]] = Field(
        default=None, description="Matched historical records; empty or omitted when none matched."
    )
    briefing: Optional[HistoricalBriefing] = Field(
        default=None,
        description=(
            "Grounded historical explanation for the identified entity, built "
            "from the retrieved sources (quoted sentences with citation "
            "indexes). Omitted when nothing was retrieved."
        ),
    )
    warnings: Optional[list[str]] = Field(
        default=None, description="Non-fatal warnings and uncertainty notes."
    )
    error: Optional[str] = Field(
        default=None, description="Failure detail when status == 'failed'."
    )
