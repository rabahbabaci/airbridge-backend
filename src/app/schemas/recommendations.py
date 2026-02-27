from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.trips import TripPreferences


class ConfidenceLevel(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class SegmentDetail(BaseModel):
    """A single segment of the recommended journey."""

    id: str = Field(..., description="Segment identifier")
    label: str = Field(..., description="Short label for the segment")
    duration_minutes: int = Field(..., ge=0, description="Duration in minutes")
    advice: str = Field("", description="Optional advice for this segment")


class RecommendationRequest(BaseModel):
    trip_id: str = Field(..., description="Trip ID returned from POST /v1/trips")


class RecommendationRecomputeRequest(BaseModel):
    trip_id: str = Field(..., description="Trip ID to recompute recommendation for")
    reason: str | None = Field(
        None, description="Optional reason for recompute, e.g. 'traffic_update'"
    )
    preference_overrides: TripPreferences | None = Field(
        None,
        description="Optional overrides for transport_mode, confidence_profile, bag_count, etc.",
    )


class RecommendationResponse(BaseModel):
    trip_id: str
    leave_home_at: datetime = Field(
        ..., description="Recommended time to leave home (UTC)"
    )
    confidence: ConfidenceLevel
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score between 0 and 1"
    )
    explanation: str = Field(
        ..., description="Human-readable explanation of the recommendation"
    )
    segments: list[SegmentDetail] = Field(
        default_factory=list,
        description="Ordered journey segments with duration and advice",
    )
    computed_at: datetime = Field(
        ..., description="Timestamp when recommendation was computed (UTC)"
    )
