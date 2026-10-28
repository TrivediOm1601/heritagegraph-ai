"""B2 — entity schemas (canonical contract).

Mirrors ``frontend/src/types/api.ts`` field for field; the frontend types
were consolidated there in A5 and this module is the backend half of the
same contract.
"""

from typing import Optional

from pydantic import BaseModel, Field


class AnalysisEntity(BaseModel):
    """One entity detected in an analysis' extracted text."""

    id: str = Field(
        min_length=1, max_length=128, description="Entity id, unique within the analysis."
    )
    name: str = Field(
        min_length=1, max_length=512, description="Entity text as extracted from the image."
    )
    type: str = Field(
        min_length=1,
        max_length=64,
        description="Category as reported by the backend (person, place, event, date, ...).",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="Optional 0..1 extraction confidence; null when not reported.",
    )
