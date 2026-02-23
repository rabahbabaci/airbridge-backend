from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ConfidenceLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class RecommendationRequest(BaseModel):
    trip_id: str = Field(..., description="Trip ID returned from POST /v1/trips")


class RecommendationRecomputeRequest(BaseModel):
    trip_id: str = Field(..., description="Trip ID to recompute recommendation for")
    reason: str | None = Field(None, description="Optional reason for recompute, e.g. 'traffic_update'")


class RecommendationResponse(BaseModel):
    trip_id: str
    leave_home_at: datetime = Field(..., description="Recommended time to leave home (UTC)")
    confidence: ConfidenceLevel
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0 and 1")
    explanation: str = Field(..., description="Human-readable explanation of the recommendation")
    segments: list[str] = Field(default_factory=list, description="Ordered list of journey segments")
    computed_at: datetime = Field(..., description="Timestamp when recommendation was computed (UTC)")
