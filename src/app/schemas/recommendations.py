from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.trips import TripPreferenceOverrides


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
    trip_id: str = Field(..., max_length=50, description="Trip ID returned from POST /v1/trips")


class RecommendationRecomputeRequest(BaseModel):
    trip_id: str = Field(..., max_length=50, description="Trip ID to recompute recommendation for")
    reason: str | None = Field(
        None, max_length=100, description="Optional reason for recompute, e.g. 'traffic_update'"
    )
    preference_overrides: TripPreferenceOverrides | None = Field(
        None,
        description="Optional overrides for transport_mode, confidence_profile, bag_count, etc.",
    )
    home_address: str | None = Field(
        None,
        max_length=500,
        description="Optional new home address to use for this recompute",
    )
    flight_number: str | None = Field(
        None,
        max_length=10,
        description="Optional flight number override for edit-mode preview (not persisted)",
    )
    departure_date: str | None = Field(
        None,
        max_length=10,
        description="Optional departure date override for edit-mode preview (not persisted)",
    )
    selected_departure_utc: str | None = Field(
        None,
        max_length=50,
        description="Optional selected departure UTC override for edit-mode preview (not persisted)",
    )


class RecommendationResponse(BaseModel):
    trip_id: str
    leave_home_at: datetime = Field(
        ..., description="Recommended time to leave home (UTC)"
    )
    gate_arrival_utc: datetime | None = Field(
        None, description="Estimated gate arrival time (UTC)"
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
    leave_home_in_past: bool = Field(
        False,
        description="True if the recommended departure time is in the past",
    )
    tier: str = Field("free", description="User tier: 'pro' or 'free'")
    remaining_pro_trips: int | None = Field(
        None, description="Pro trips remaining before downgrade, None if subscribed or anonymous"
    )
    terminal_coordinates: dict | None = Field(
        None, description="Lat/lng of the departure terminal, if available"
    )
    home_coordinates: dict | None = Field(
        None, description="Lat/lng of the user's home address, if available"
    )
    origin_airport_code: str | None = Field(
        None, description="Origin airport IATA code"
    )
