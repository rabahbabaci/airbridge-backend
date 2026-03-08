"""Recommendation engine: lead time from preferences, flight snapshot, and integrations."""

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
from app.services.integrations.airport_defaults import get_airport_timings
from app.services.integrations.google_maps import get_airport_destination, get_drive_time
from app.services.integrations.tsa_estimator import estimate_tsa_wait
from app.services.trip_intake import get_trip_context

CONFIDENCE_MULTIPLIERS: dict[ConfidenceProfile, float] = {
    ConfidenceProfile.safety: 1.25,
    ConfidenceProfile.sweet: 1.0,
    ConfidenceProfile.risk: 0.85,
}

CONFIDENCE_SCORES: dict[ConfidenceProfile, float] = {
    ConfidenceProfile.safety: 0.92,
    ConfidenceProfile.sweet: 0.85,
    ConfidenceProfile.risk: 0.70,
}

RIDESHARE_PICKUP_WAIT_MINUTES = 5


def _effective_context(
    context: TripContext, overrides: TripPreferences | None
) -> TripContext:
    """Apply preference_overrides onto a copy of context (only non-None overrides)."""
    if not overrides:
        return context
    prefs_updates: dict[str, object] = {}
    if overrides.transport_mode is not None:
        prefs_updates["transport_mode"] = overrides.transport_mode
    if overrides.confidence_profile is not None:
        prefs_updates["confidence_profile"] = overrides.confidence_profile
    if overrides.bag_count is not None:
        prefs_updates["bag_count"] = overrides.bag_count
    if overrides.traveling_with_children is not None:
        prefs_updates["traveling_with_children"] = overrides.traveling_with_children
    if overrides.extra_time_minutes is not None:
        prefs_updates["extra_time_minutes"] = overrides.extra_time_minutes
    new_prefs = context.preferences.model_copy(update=prefs_updates)
    return context.model_copy(update={"preferences": new_prefs})


def _compute_segments(context: TripContext, snapshot: FlightSnapshot) -> list[SegmentDetail]:
    origin_iata = snapshot.origin_airport_code or ""
    timings = get_airport_timings(origin_iata)
    prefs = context.preferences
    segments: list[SegmentDetail] = []

    # 1. Transport to airport (travel time)
    drive_data = get_drive_time(
        context.home_address,
        origin_iata,
        transport_mode=prefs.transport_mode.value,
    )
    drive_minutes = drive_data["duration_minutes"]
    if prefs.transport_mode == TransportMode.rideshare:
        drive_minutes += RIDESHARE_PICKUP_WAIT_MINUTES
    segments.append(
        SegmentDetail(
            id="transport",
            label=drive_data.get("label", f"Drive to {origin_iata or 'airport'}"),
            duration_minutes=drive_minutes,
            advice=f"{drive_data.get('duration_text', '')} — {drive_data.get('distance_text', '')}".strip(" — "),
        )
    )

    # 2. At Airport (time at curb/check-in area)
    curb_min = timings["curb_to_checkin"]
    terminal_info = f"Terminal {snapshot.departure_terminal}" if snapshot.departure_terminal else "Arrive at terminal"
    if curb_min > 0:
        segments.append(
            SegmentDetail(
                id="at_airport",
                label="At Airport",
                duration_minutes=curb_min,
                advice=terminal_info,
            )
        )

    # 3. Bag drop (only if bags)
    bag_count = prefs.bag_count or 0
    if bag_count > 0:
        bag_minutes = 5 + (bag_count - 1) * 3
        segments.append(
            SegmentDetail(
                id="bag_drop",
                label="Bag Drop",
                duration_minutes=bag_minutes,
                advice=f"{bag_count} bag(s)",
            )
        )

    # 4. TSA Security — walk to security is the "travel" part, TSA wait is the "at" part
    # We encode both in one segment: duration = walk + wait, advice includes the split
    checkin_to_sec = timings["checkin_to_security"]
    departure_hour = snapshot.scheduled_departure.hour if snapshot.scheduled_departure else 12
    tsa = estimate_tsa_wait(origin_iata, departure_hour)
    tsa_wait = tsa["estimated_minutes"]
    tsa_total = checkin_to_sec + tsa_wait
    tsa_period = tsa.get("period", "")
    segments.append(
        SegmentDetail(
            id="tsa",
            label=f"TSA Security ({origin_iata})" if origin_iata else "TSA Security",
            duration_minutes=tsa_total,
            advice=f"walk:{checkin_to_sec}|wait:{tsa_wait}|{tsa_period}",
        )
    )

    # 5. Gate (walk from security to gate)
    gate_walk = timings["security_to_gate"]
    gate_advice = f"Terminal {snapshot.departure_terminal}" if snapshot.departure_terminal else "Arrive at gate"
    segments.append(
        SegmentDetail(
            id="walk_to_gate",
            label="Gate",
            duration_minutes=gate_walk,
            advice=gate_advice,
        )
    )

    return segments


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
    prefs = context.preferences
    segments = _compute_segments(context, snapshot)
    raw_total = sum(s.duration_minutes for s in segments)

    # Apply confidence multiplier to get adjusted total
    multiplier = CONFIDENCE_MULTIPLIERS.get(prefs.confidence_profile, 1.0)
    adjusted_total = int(round(raw_total * multiplier))
    multiplier_extra = adjusted_total - raw_total

    # Additional buffers
    children_extra = 15 if prefs.traveling_with_children else 0
    extra_time = prefs.extra_time_minutes or 0

    # Total extra minutes from all sources
    total_extra = multiplier_extra + children_extra + extra_time

    # Add a visible "Comfort buffer" segment if there's any extra time
    if total_extra > 0:
        advice_parts = []
        if multiplier_extra > 0:
            profile_name = prefs.confidence_profile.value.replace("_", " ").title()
            advice_parts.append(f"{profile_name} profile buffer")
        if children_extra > 0:
            advice_parts.append("traveling with children")
        if extra_time > 0:
            advice_parts.append(f"+{extra_time} min extra time")
        segments.append(
            SegmentDetail(
                id="comfort_buffer",
                label="Comfort buffer",
                duration_minutes=total_extra,
                advice=", ".join(advice_parts),
            )
        )

    # Now the total of all segments includes everything
    final_total = sum(s.duration_minutes for s in segments)

    # Boarding starts 30 min before departure
    boarding_time = snapshot.scheduled_departure - timedelta(minutes=30)
    leave_home_at = boarding_time - timedelta(minutes=final_total)

    # Gate arrival = leave_home_at + all segments except comfort_buffer
    # (comfort buffer is spent at the gate)
    gate_segments_total = sum(
        s.duration_minutes for s in segments if s.id != "comfort_buffer"
    )
    gate_arrival_at = leave_home_at + timedelta(minutes=gate_segments_total)

    confidence_score = CONFIDENCE_SCORES.get(prefs.confidence_profile, 0.85)

    transport_label = segments[0].label if segments else "Drive to airport"
    explanation = (
        f"{transport_label}, {prefs.confidence_profile.value.replace('_', ' ').title()} profile. "
        f"Raw journey: {raw_total} min, with {total_extra} min buffer."
    )
    if prefs.traveling_with_children:
        explanation += " Includes +15 min for children."
    if extra_time > 0:
        explanation += f" Includes +{extra_time} min extra time."

    return RecommendationResponse(
        trip_id=trip_id,
        leave_home_at=leave_home_at,
        gate_arrival_utc=gate_arrival_at,
        confidence=_confidence_from_profile(prefs.confidence_profile),
        confidence_score=confidence_score,
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
