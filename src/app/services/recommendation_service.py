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
from app.services.integrations.airport_graph import resolve_walking_times
from app.services.integrations.google_maps import get_airport_destination, get_drive_time
from app.services.integrations.tsa_model import estimate_tsa_wait
from app.services.trip_intake import get_trip_context

CONFIDENCE_SCORES: dict[ConfidenceProfile, float] = {
    ConfidenceProfile.safety: 0.92,
    ConfidenceProfile.sweet: 0.85,
    ConfidenceProfile.risk: 0.70,
}

RIDESHARE_PICKUP_WAIT_MINUTES = 5

GATE_BUFFER_MINUTES: dict[ConfidenceProfile, int] = {
    ConfidenceProfile.safety: 30,
    ConfidenceProfile.sweet: 15,
    ConfidenceProfile.risk: 0,
}


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
    if overrides.gate_time_minutes is not None:
        prefs_updates["gate_time_minutes"] = overrides.gate_time_minutes
    new_prefs = context.preferences.model_copy(update=prefs_updates)
    return context.model_copy(update={"preferences": new_prefs})


def _compute_segments(context: TripContext, snapshot: FlightSnapshot) -> list[SegmentDetail]:
    origin_iata = snapshot.origin_airport_code or ""
    timings = get_airport_timings(origin_iata)
    prefs = context.preferences
    segments: list[SegmentDetail] = []

    # 1. Transport to airport (travel time)
    approx_leave = snapshot.scheduled_departure - timedelta(hours=3)
    departure_ts = int(approx_leave.timestamp())
    drive_data = get_drive_time(
        context.home_address,
        origin_iata,
        transport_mode=prefs.transport_mode.value,
        departure_time=departure_ts,
        terminal=snapshot.departure_terminal,
    )
    if prefs.confidence_profile == ConfidenceProfile.safety:
        drive_minutes = drive_data["duration_pessimistic"]
    elif prefs.confidence_profile == ConfidenceProfile.risk:
        drive_minutes = drive_data["duration_optimistic"]
    else:
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

    # Resolve walking times: try graph first, fall back to flat defaults
    graph_times = resolve_walking_times(
        airport_iata=origin_iata,
        transport_mode=prefs.transport_mode.value,
        terminal=snapshot.departure_terminal,
        gate=snapshot.departure_gate,
        with_children=prefs.traveling_with_children,
    )
    using_graph = graph_times is not None

    if using_graph:
        walk_dropoff_to_checkin = graph_times["entry_to_checkin"]
        walk_checkin_to_security = graph_times["checkin_to_tsa"]
        walk_security_to_gate = graph_times["tsa_to_gate"]
    else:
        # Flat defaults from airport_defaults.py
        if prefs.transport_mode in (TransportMode.train, TransportMode.bus):
            walk_dropoff_to_checkin = timings["transit_to_terminal"]
        elif prefs.transport_mode == TransportMode.driving:
            walk_dropoff_to_checkin = timings["parking_to_terminal"]
        else:
            walk_dropoff_to_checkin = timings["curb_to_checkin"]
        walk_checkin_to_security = timings["checkin_to_security"]
        walk_security_to_gate = timings["security_to_gate"]

    # 2. At Airport — arrival waypoint
    bag_count = prefs.bag_count or 0
    has_boarding_pass = prefs.has_boarding_pass
    terminal_info = f"T{snapshot.departure_terminal}" if snapshot.departure_terminal else ""
    gate_info = f" Gate {snapshot.departure_gate}" if snapshot.departure_gate else ""
    at_airport_detail = f"{terminal_info}{gate_info}".strip() if using_graph else ""

    if bag_count > 0:
        # With bags: walk from drop-off to check-in counter
        segments.append(
            SegmentDetail(
                id="at_airport",
                label="At Airport",
                duration_minutes=walk_dropoff_to_checkin,
                advice=f"walk_to_next:{walk_dropoff_to_checkin}|{at_airport_detail}".rstrip("|"),
            )
        )
    elif not has_boarding_pass:
        # No bags but need boarding pass: walk to check-in counter
        segments.append(
            SegmentDetail(
                id="at_airport",
                label="At Airport",
                duration_minutes=walk_dropoff_to_checkin,
                advice=f"walk_to_next:{walk_dropoff_to_checkin}|{at_airport_detail}".rstrip("|"),
            )
        )
    else:
        # Has boarding pass, no bags: walk from drop-off straight to TSA
        walk_to_tsa = walk_dropoff_to_checkin + walk_checkin_to_security
        segments.append(
            SegmentDetail(
                id="at_airport",
                label="At Airport",
                duration_minutes=walk_to_tsa,
                advice=f"walk_to_next:{walk_to_tsa}|{at_airport_detail}".rstrip("|"),
            )
        )

    # 3. Check-in / Bag drop
    if bag_count > 0:
        if has_boarding_pass:
            bag_drop_time = 5 + (bag_count - 1) * 3
            bag_advice = f"{bag_count} bag(s)|drop:{bag_drop_time}|walk_to_next:{walk_checkin_to_security}"
        else:
            bag_drop_time = 8 + (bag_count - 1) * 3
            bag_advice = f"Get boarding pass + drop {bag_count} bag(s)|counter:{bag_drop_time}|walk_to_next:{walk_checkin_to_security}"
        bag_total = bag_drop_time + walk_checkin_to_security
        segments.append(
            SegmentDetail(
                id="bag_drop",
                label="Bag Drop",
                duration_minutes=bag_total,
                advice=bag_advice,
            )
        )
    elif not has_boarding_pass:
        # No bags, no boarding pass: stop at counter then walk to TSA
        counter_time = 5
        checkin_total = counter_time + walk_checkin_to_security
        segments.append(
            SegmentDetail(
                id="checkin",
                label="Check-in",
                duration_minutes=checkin_total,
                advice=f"Get boarding pass at counter|counter:{counter_time}|walk_to_next:{walk_checkin_to_security}",
            )
        )

    # 4. TSA Security — ONLY the wait time, no walking included
    departure_hour = snapshot.scheduled_departure.hour if snapshot.scheduled_departure else 12
    dow = snapshot.scheduled_departure.weekday()  # 0=Monday
    tsa = estimate_tsa_wait(
        airport_iata=origin_iata,
        departure_hour=departure_hour,
        day_of_week=dow,
        security_access=prefs.security_access.value if hasattr(prefs, "security_access") else "none",
    )
    if prefs.confidence_profile == ConfidenceProfile.safety:
        tsa_wait = tsa["p80"]
    elif prefs.confidence_profile == ConfidenceProfile.risk:
        tsa_wait = tsa["p25"]
    else:
        tsa_wait = tsa["p50"]
    segments.append(
        SegmentDetail(
            id="tsa",
            label=f"TSA Security ({origin_iata})" if origin_iata else "TSA Security",
            duration_minutes=tsa_wait,
            advice=f"wait:{tsa_wait}|range:{tsa['p25']}-{tsa['p75']}|{prefs.security_access.value}",
        )
    )

    # 5. Gate (walk from security to gate)
    gate_walk = walk_security_to_gate
    if snapshot.departure_gate:
        gate_advice = f"Gate {snapshot.departure_gate}"
        if snapshot.departure_terminal:
            gate_advice += f" (Terminal {snapshot.departure_terminal})"
    elif snapshot.departure_terminal:
        gate_advice = f"Terminal {snapshot.departure_terminal}"
    else:
        gate_advice = "Arrive at gate"
    segments.append(
        SegmentDetail(
            id="walk_to_gate",
            label="Gate",
            duration_minutes=gate_walk,
            advice=gate_advice,
        )
    )

    # 6. Gate buffer — time at gate before boarding starts
    if prefs.gate_time_minutes is not None:
        gate_buffer = prefs.gate_time_minutes
    else:
        gate_buffer = GATE_BUFFER_MINUTES.get(prefs.confidence_profile, 15)
    if gate_buffer > 0:
        segments.append(
            SegmentDetail(
                id="gate_buffer",
                label="Time at gate",
                duration_minutes=gate_buffer,
                advice="Settle in before boarding",
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

    # Additional buffers (no more flat multiplier — transport already reflects profile)
    children_extra = 15 if prefs.traveling_with_children else 0
    extra_time = prefs.extra_time_minutes or 0
    total_extra = children_extra + extra_time

    if total_extra > 0:
        advice_parts = []
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

    final_total = sum(s.duration_minutes for s in segments)

    # Boarding starts 30 min before departure
    boarding_time = snapshot.scheduled_departure - timedelta(minutes=30)
    leave_home_at = boarding_time - timedelta(minutes=final_total)

    # Gate arrival = leave_home_at + segment durations (without comfort buffer or gate buffer)
    gate_segments_total = sum(
        s.duration_minutes for s in segments if s.id not in ("comfort_buffer", "gate_buffer")
    )
    gate_arrival_at = leave_home_at + timedelta(minutes=gate_segments_total)

    confidence_score = CONFIDENCE_SCORES.get(prefs.confidence_profile, 0.85)

    transport_label = segments[0].label if segments else "Drive to airport"
    profile_name = prefs.confidence_profile.value.replace("_", " ").title()
    explanation = (
        f"{transport_label}, {profile_name} profile. "
        f"Journey: {raw_total} min"
    )
    if total_extra > 0:
        explanation += f", with {total_extra} min buffer."
    else:
        explanation += "."
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
