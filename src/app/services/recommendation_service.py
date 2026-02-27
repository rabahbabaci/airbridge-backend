"""Recommendation engine v0.5: deterministic lead time from preferences and flight snapshot."""

from datetime import datetime, timedelta, timezone

from app.schemas.flight_snapshot import FlightSnapshot
from app.schemas.recommendations import (
    ConfidenceLevel,
    RecommendationRecomputeRequest,
    RecommendationRequest,
    RecommendationResponse,
    SegmentDetail,
)
from app.schemas.trips import (
    ConfidenceProfile,
    TransportMode,
    TripContext,
    TripPreferences,
)
from app.services.flight_snapshot_service import build_flight_snapshot
from app.services.trip_intake import get_trip_context

# Lead time and modifiers (tunable constants)
BASE_LEAD_TIME_MINUTES = 90
GATE_BUFFER_MINUTES = 15
TRANSPORT_OFFSET_MINUTES: dict[TransportMode, int] = {
    TransportMode.rideshare: 20,
    TransportMode.driving: 10,
    TransportMode.train: 25,
    TransportMode.bus: 25,
    TransportMode.other: 15,
}
MINUTES_PER_BAG = 7
MINUTES_WITH_CHILDREN = 10
CONFIDENCE_MULTIPLIERS: dict[ConfidenceProfile, float] = {
    ConfidenceProfile.safety: 1.25,
    ConfidenceProfile.sweet: 1.0,
    ConfidenceProfile.risk: 0.85,
}


def _effective_context(
    context: TripContext, overrides: TripPreferences | None
) -> TripContext:
    """Apply preference_overrides onto a copy of context (only non-None overrides)."""
    if not overrides:
        return context
    updates: dict[str, object] = {}
    if overrides.transport_mode is not None:
        updates["transport_mode"] = overrides.transport_mode
    if overrides.confidence_profile is not None:
        updates["confidence_profile"] = overrides.confidence_profile
    if overrides.bag_count is not None:
        updates["bag_count"] = overrides.bag_count
    if overrides.traveling_with_children is not None:
        updates["traveling_with_children"] = overrides.traveling_with_children
    if overrides.extra_time_minutes is not None:
        updates["extra_time_minutes"] = overrides.extra_time_minutes
    return context.model_copy(update=updates)


def _compute_lead_minutes(
    context: TripContext, snapshot: FlightSnapshot
) -> tuple[int, list[SegmentDetail], list[str]]:
    """
    Compute total lead time in minutes and segment breakdown.
    Returns (total_minutes, segments, explanation_parts for modifiers).
    """
    transport_offset = TRANSPORT_OFFSET_MINUTES.get(context.transport_mode, 15)
    airport_baseline = (
        snapshot.airport_timings.base_tsa_minutes
        + snapshot.airport_timings.check_in_buffer_minutes
    )
    mult = CONFIDENCE_MULTIPLIERS.get(context.confidence_profile, 1.0)

    # Core time (before multiplier): base lead + transport + airport + gate
    core_minutes = (
        BASE_LEAD_TIME_MINUTES
        + transport_offset
        + airport_baseline
        + GATE_BUFFER_MINUTES
    )
    # Modifiers added directly (then we add extra_time at end)
    bag_minutes = context.bag_count * MINUTES_PER_BAG
    children_minutes = MINUTES_WITH_CHILDREN if context.traveling_with_children else 0
    modifier_minutes = bag_minutes + children_minutes + context.extra_time_minutes

    total_minutes = int(round(core_minutes * mult)) + modifier_minutes

    # Segment durations (proportional to core components, scaled by mult), then add modifier as "extra buffer"
    home_buffer = int(round(BASE_LEAD_TIME_MINUTES * mult))
    transport_dur = int(round(transport_offset * mult))
    security_dur = int(round(airport_baseline * mult))
    gate_dur = int(round(GATE_BUFFER_MINUTES * mult))
    # If modifier_minutes > 0, add as final segment
    segments = [
        SegmentDetail(
            id="home_buffer",
            label="Home buffer",
            duration_minutes=home_buffer,
            advice="Leave home in time for transport.",
        ),
        SegmentDetail(
            id="transport",
            label="Transport to airport",
            duration_minutes=transport_dur,
            advice=f"Allow time for {context.transport_mode.value}.",
        ),
        SegmentDetail(
            id="check_in_security",
            label="Check-in & security",
            duration_minutes=security_dur,
            advice="TSA and check-in buffer.",
        ),
        SegmentDetail(
            id="gate_buffer",
            label="Gate buffer",
            duration_minutes=gate_dur,
            advice="Reach gate before boarding.",
        ),
    ]
    if modifier_minutes > 0:
        segments.append(
            SegmentDetail(
                id="extra_buffer",
                label="Extra buffer",
                duration_minutes=modifier_minutes,
                advice="Bags, children, and extra time.",
            )
        )

    explanation_parts: list[str] = []
    if context.bag_count > 0:
        explanation_parts.append(f"+{bag_minutes} min for {context.bag_count} bag(s)")
    if context.traveling_with_children:
        explanation_parts.append(f"+{MINUTES_WITH_CHILDREN} min for kids")
    if context.extra_time_minutes > 0:
        explanation_parts.append(f"+{context.extra_time_minutes} min extra buffer")

    return total_minutes, segments, explanation_parts


def _confidence_from_profile(profile: ConfidenceProfile) -> ConfidenceLevel:
    if profile == ConfidenceProfile.safety:
        return ConfidenceLevel.high
    if profile == ConfidenceProfile.risk:
        return ConfidenceLevel.low
    return ConfidenceLevel.medium


def _build_response(
    trip_id: str,
    context: TripContext,
    snapshot: FlightSnapshot,
    computed_at: datetime,
) -> RecommendationResponse:
    total_minutes, segments, modifier_parts = _compute_lead_minutes(context, snapshot)
    leave_home_at = snapshot.scheduled_departure - timedelta(minutes=total_minutes)

    modifier_text = ""
    if modifier_parts:
        modifier_text = " " + ", ".join(modifier_parts) + "."

    explanation = (
        f"Base lead {BASE_LEAD_TIME_MINUTES} min + airport baseline ({snapshot.airport_timings.base_tsa_minutes}+{snapshot.airport_timings.check_in_buffer_minutes} min) "
        f"+ {context.transport_mode.value} offset, {context.confidence_profile.value} profile."
        f"{modifier_text}"
    )

    return RecommendationResponse(
        trip_id=trip_id,
        leave_home_at=leave_home_at,
        confidence=_confidence_from_profile(context.confidence_profile),
        confidence_score=0.85
        if context.confidence_profile == ConfidenceProfile.sweet
        else (0.9 if context.confidence_profile == ConfidenceProfile.safety else 0.7),
        explanation=explanation.strip(),
        segments=segments,
        computed_at=computed_at,
    )


def compute_recommendation(
    payload: RecommendationRequest,
) -> RecommendationResponse | None:
    """
    Compute leave-home recommendation for the given trip.
    Returns None if trip_id is not found (caller should return 404).
    """
    context = get_trip_context(payload.trip_id)
    if context is None:
        return None
    snapshot = build_flight_snapshot(context)
    now = datetime.now(tz=timezone.utc)
    return _build_response(str(context.trip_id), context, snapshot, now)


def recompute_recommendation(
    payload: RecommendationRecomputeRequest,
) -> RecommendationResponse | None:
    """
    Recompute recommendation; uses preference_overrides when provided.
    Returns None if trip_id is not found.
    """
    context = get_trip_context(payload.trip_id)
    if context is None:
        return None
    context = _effective_context(context, payload.preference_overrides)
    snapshot = build_flight_snapshot(context)
    now = datetime.now(tz=timezone.utc)
    response = _build_response(payload.trip_id, context, snapshot, now)
    if payload.reason:
        response.explanation = f"[Recompute: {payload.reason}] " + response.explanation
    return response
