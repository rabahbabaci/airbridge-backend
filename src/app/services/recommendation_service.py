from datetime import datetime, timedelta, timezone

from app.schemas.recommendations import (
    ConfidenceLevel,
    RecommendationRecomputeRequest,
    RecommendationRequest,
    RecommendationResponse,
)

_PLACEHOLDER_LEAD_TIME_HOURS = 3


def _build_placeholder_response(trip_id: str, computed_at: datetime) -> RecommendationResponse:
    leave_home_at = computed_at + timedelta(hours=_PLACEHOLDER_LEAD_TIME_HOURS)
    return RecommendationResponse(
        trip_id=trip_id,
        leave_home_at=leave_home_at,
        confidence=ConfidenceLevel.medium,
        confidence_score=0.75,
        explanation=(
            "Placeholder recommendation: leave 3 hours before the current time. "
            "Real computation will factor in transport, TSA, and flight context."
        ),
        segments=[
            "home → transport pickup",
            "transport → airport",
            "check-in + security",
            "gate buffer",
        ],
        computed_at=computed_at,
    )


def compute_recommendation(payload: RecommendationRequest) -> RecommendationResponse:
    """
    Compute a leave-home recommendation for the given trip.
    Placeholder: returns a fixed 3-hour lead time with medium confidence.
    """
    now = datetime.now(tz=timezone.utc)
    return _build_placeholder_response(payload.trip_id, now)


def recompute_recommendation(payload: RecommendationRecomputeRequest) -> RecommendationResponse:
    """
    Recompute recommendation for an existing trip, optionally given a change reason.
    Placeholder: same logic as compute, with reason noted in explanation.
    """
    now = datetime.now(tz=timezone.utc)
    response = _build_placeholder_response(payload.trip_id, now)
    if payload.reason:
        response.explanation = f"[Recompute triggered by: {payload.reason}] " + response.explanation
    return response
