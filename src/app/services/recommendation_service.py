"""Recommendation engine: lead time from preferences, flight snapshot, and integrations."""

import math
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
    TripPreferenceOverrides,
)
from app.services.flight_snapshot_service import build_flight_snapshot
from app.services.integrations.airport_defaults import get_airport_timings
from app.services.integrations.airport_graph import resolve_walking_times
from app.services.integrations.google_maps import (
    geocode_address,
    get_drive_time,
    get_terminal_coordinates,
)
from app.services.integrations.tsa_api import fetch_live_tsa_wait
from app.services.integrations.tsa_model import estimate_tsa_wait
from app.services.trial import get_tier_info
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


def build_latest_recommendation_jsonb(response) -> dict:
    """Marshal a RecommendationResponse into the latest_recommendation JSONB shape.

    Stored on the trip row by the track endpoint and the polling agent so the
    Active Trip Screen can render segments + map coordinates without a round-trip
    to /v1/recommendations.
    """
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    segments = [
        {
            "id": seg.id,
            "label": seg.label,
            "duration_minutes": seg.duration_minutes,
            "advice": seg.advice,
        }
        for seg in (response.segments or [])
    ]
    return {
        "segments": segments,
        "home_coordinates": response.home_coordinates,
        "terminal_coordinates": response.terminal_coordinates,
        "computed_at": now_iso,
    }


def _effective_context(
    context: TripContext, overrides: TripPreferenceOverrides | None
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
    if overrides.security_access is not None:
        prefs_updates["security_access"] = overrides.security_access
    if overrides.has_boarding_pass is not None:
        prefs_updates["has_boarding_pass"] = overrides.has_boarding_pass
    new_prefs = context.preferences.model_copy(update=prefs_updates)
    return context.model_copy(update={"preferences": new_prefs})


async def _compute_segments(context: TripContext, snapshot: FlightSnapshot) -> list[SegmentDetail]:
    origin_iata = snapshot.origin_airport_code or ""
    timings = get_airport_timings(origin_iata)
    prefs = context.preferences
    segments: list[SegmentDetail] = []

    # 1. Transport to airport (travel time)
    approx_leave = snapshot.scheduled_departure - timedelta(hours=3)
    departure_ts = int(approx_leave.timestamp())
    drive_data = await get_drive_time(
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

        # 1b. Parking segment (graph path, driving only)
        # The graph's entry_to_checkin includes parking walk time for drivers.
        # Extract it as a separate segment using airport_defaults values.
        if prefs.transport_mode == TransportMode.driving:
            if prefs.traveling_with_children:
                full_parking = math.ceil(timings["parking_to_terminal"] * 1.4)
                walk_curb = math.ceil(timings["curb_to_checkin"] * 1.4)
            else:
                full_parking = timings["parking_to_terminal"]
                walk_curb = timings["curb_to_checkin"]
            parking_min = full_parking - walk_curb
            if parking_min > 0:
                segments.append(SegmentDetail(
                    id="parking",
                    label="Parking",
                    duration_minutes=parking_min,
                    advice=f"Park & walk to terminal at {origin_iata}" if origin_iata else "Park & walk to terminal",
                ))
                walk_dropoff_to_checkin = max(walk_dropoff_to_checkin - parking_min, 0)
    else:
        # Flat defaults from airport_defaults.py
        if prefs.transport_mode in (TransportMode.train, TransportMode.bus):
            walk_dropoff_to_checkin = timings["transit_to_terminal"]
        elif prefs.transport_mode == TransportMode.driving:
            walk_dropoff_to_checkin = timings["curb_to_checkin"]
        else:
            walk_dropoff_to_checkin = timings["curb_to_checkin"]
        walk_checkin_to_security = timings["checkin_to_security"]
        walk_security_to_gate = timings["security_to_gate"]

        # Apply children walking multiplier on flat-default path
        if prefs.traveling_with_children:
            walk_dropoff_to_checkin = math.ceil(walk_dropoff_to_checkin * 1.4)
            walk_checkin_to_security = math.ceil(walk_checkin_to_security * 1.4)
            walk_security_to_gate = math.ceil(walk_security_to_gate * 1.4)

    # 1b. Parking segment (flat-defaults, driving only)
    if not using_graph and prefs.transport_mode == TransportMode.driving:
        # Derive parking_min so that parking + walk_dropoff_to_checkin == old at_airport total.
        # Children multiplier was already applied to walk_dropoff_to_checkin above,
        # so compute the full parking_to_terminal with the same multiplier and subtract.
        if prefs.traveling_with_children:
            full_parking = math.ceil(timings["parking_to_terminal"] * 1.4)
        else:
            full_parking = timings["parking_to_terminal"]
        parking_min = full_parking - walk_dropoff_to_checkin
        if parking_min > 0:
            segments.append(SegmentDetail(
                id="parking",
                label="Parking",
                duration_minutes=parking_min,
                advice=f"Park & walk to terminal at {origin_iata}" if origin_iata else "Park & walk to terminal",
            ))

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
    # Use local hour for TSA estimates; fall back to UTC hour if local not available
    departure_hour = snapshot.departure_local_hour
    if departure_hour is None:
        departure_hour = snapshot.scheduled_departure.hour if snapshot.scheduled_departure else 12
    dow = snapshot.scheduled_departure.weekday()  # 0=Monday
    live_tsa = await fetch_live_tsa_wait(origin_iata) if origin_iata else None
    tsa = estimate_tsa_wait(
        airport_iata=origin_iata,
        departure_hour=departure_hour,
        day_of_week=dow,
        security_access=prefs.security_access.value if hasattr(prefs, "security_access") else "none",
        live_api_data=live_tsa,
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


async def _build_response(
    trip_id: str,
    context: TripContext,
    snapshot: FlightSnapshot,
    computed_at: datetime,
    user=None,
) -> RecommendationResponse:
    prefs = context.preferences
    segments = await _compute_segments(context, snapshot)
    raw_total = sum(s.duration_minutes for s in segments)

    # Additional buffers
    extra_time = prefs.extra_time_minutes or 0
    total_extra = extra_time

    if extra_time > 0:
        segments.append(
            SegmentDetail(
                id="comfort_buffer",
                label="Comfort buffer",
                duration_minutes=extra_time,
                advice=f"+{extra_time} min extra time",
            )
        )

    final_total = sum(s.duration_minutes for s in segments)

    # Boarding starts 30 min before departure
    boarding_time = snapshot.scheduled_departure - timedelta(minutes=30)
    leave_home_at = boarding_time - timedelta(minutes=final_total)

    # Flag if leave_home_at is in the past
    leave_home_in_past = leave_home_at < computed_at

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
        explanation += " Walking times adjusted for children."
    if extra_time > 0:
        explanation += f" Includes +{extra_time} min extra time."
    if leave_home_in_past:
        explanation += " Warning: recommended departure time is in the past."

    tier, remaining_pro_trips = get_tier_info(user)

    # Resolve coordinates for map display
    origin_iata = snapshot.origin_airport_code or ""
    terminal_coords = get_terminal_coordinates(origin_iata, snapshot.departure_terminal)
    home_coords = geocode_address(context.home_address)

    return RecommendationResponse(
        trip_id=trip_id,
        leave_home_at=leave_home_at,
        gate_arrival_utc=gate_arrival_at,
        confidence=_confidence_from_profile(prefs.confidence_profile),
        confidence_score=confidence_score,
        explanation=explanation.strip(),
        segments=segments,
        computed_at=computed_at,
        leave_home_in_past=leave_home_in_past,
        tier=tier,
        remaining_pro_trips=remaining_pro_trips,
        terminal_coordinates=terminal_coords,
        home_coordinates=home_coords,
        origin_airport_code=origin_iata,
    )


async def compute_recommendation(
    payload: RecommendationRequest,
    user=None,
    *,
    strict: bool = False,
) -> RecommendationResponse | None:
    """
    Compute leave-home recommendation for the given trip.
    Returns None if trip_id is not found (caller should return 404).

    ``strict`` threads into build_flight_snapshot — see its docstring.
    Route handlers for user-initiated compute set strict=True so ADB
    outages surface as HTTP 503; background callers leave it False.
    """
    context = await get_trip_context(payload.trip_id)
    if context is None:
        return None
    snapshot = build_flight_snapshot(context, strict=strict)
    now = datetime.now(tz=timezone.utc)
    return await _build_response(str(context.trip_id), context, snapshot, now, user=user)


async def recompute_recommendation(
    payload: RecommendationRecomputeRequest,
    user=None,
    *,
    prefetched_snapshot: FlightSnapshot | None = None,
    strict: bool = False,
) -> RecommendationResponse | None:
    """
    Recompute recommendation; uses preference_overrides when provided.
    Returns None if trip_id is not found.

    ``prefetched_snapshot`` lets callers (the polling agent) skip the ADB call
    when a valid FlightSnapshot has already been reconstructed from the
    persisted flight_info/flight_status columns. Trip-level overrides
    (flight_number / departure_date / selected_departure_utc) force the fresh
    build_flight_snapshot path even if a prefetched snapshot is passed —
    edit-mode preview can't trust the stored snapshot.
    """
    context = await get_trip_context(payload.trip_id)
    if context is None:
        return None
    context = _effective_context(context, payload.preference_overrides)
    # Apply trip-level overrides for edit-mode preview (not persisted to DB)
    trip_updates = {}
    if payload.flight_number is not None:
        trip_updates["flight_number"] = payload.flight_number
    if payload.departure_date is not None:
        trip_updates["departure_date"] = payload.departure_date
    if payload.selected_departure_utc is not None:
        trip_updates["selected_departure_utc"] = payload.selected_departure_utc
    if trip_updates:
        context = context.model_copy(update=trip_updates)
        # Edit-mode preview: stored flight_info is for the saved flight, not the
        # hypothetical one being previewed. Force fresh ADB path.
        prefetched_snapshot = None

    snapshot = prefetched_snapshot or build_flight_snapshot(context, strict=strict)
    now = datetime.now(tz=timezone.utc)
    response = await _build_response(payload.trip_id, context, snapshot, now, user=user)
    if payload.reason:
        response.explanation = f"[Recompute: {payload.reason}] " + response.explanation
    return response
