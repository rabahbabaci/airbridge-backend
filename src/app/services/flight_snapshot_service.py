"""Build flight snapshot from trip context. Placeholder data for now; real integrations plug in later."""

from datetime import datetime, timezone, timedelta

from app.schemas.flight_snapshot import AirportTimings, FlightSnapshot
from app.schemas.trips import TripContext, DepartureTimeWindow

TIME_WINDOW_MINUTES: dict[DepartureTimeWindow, tuple[int, int] | None] = {
    DepartureTimeWindow.morning: (6 * 60, 11 * 60 + 59),
    DepartureTimeWindow.midday: (12 * 60, 14 * 60 + 59),
    DepartureTimeWindow.afternoon: (15 * 60, 17 * 60 + 59),
    DepartureTimeWindow.evening: (18 * 60, 21 * 60 + 59),
    # late night wraps past midnight; handle on use (end < start)
    DepartureTimeWindow.late_night: (22 * 60, 5 * 60 + 59),
    DepartureTimeWindow.not_sure: None,
}

_AIRPORT_TIMINGS_OVERRIDES: dict[str, dict[str, int]] = {
    "SFO": {
        "security_minutes": 30,
        "parking_to_terminal_minutes": 12,
        "transit_station_to_terminal_minutes": 15,  # BART + AirTrain
    },
    "OAK": {
        "security_minutes": 20,
        "parking_to_terminal_minutes": 8,
        "transit_station_to_terminal_minutes": 10,  # BART + walk
    },
    "SJC": {
        "security_minutes": 20,
        "parking_to_terminal_minutes": 8,
        "transit_station_to_terminal_minutes": 12,
    },
}


def get_time_window_minutes(
    window: DepartureTimeWindow | None,
) -> tuple[int, int] | None:
    if window is None:
        return None
    return TIME_WINDOW_MINUTES.get(window)


def _round_to_half_hour_minutes(minutes_since_midnight: float) -> int:
    return int(round(minutes_since_midnight / 30.0) * 30)


def _scheduled_departure_from_window(trip_context: TripContext) -> datetime:
    # Default placeholder: 10:00 UTC on departure_date
    year = trip_context.departure_date.year
    month = trip_context.departure_date.month
    day = trip_context.departure_date.day
    default_departure = datetime(year, month, day, 10, 0, 0, tzinfo=timezone.utc)

    if trip_context.input_mode != "route_search":
        return default_departure
    if (
        trip_context.departure_time_window is None
        or trip_context.departure_time_window == DepartureTimeWindow.not_sure
    ):
        return default_departure

    # Requirement: late_night wraps past midnight; use 01:30 UTC next day as midpoint.
    if trip_context.departure_time_window == DepartureTimeWindow.late_night:
        return datetime(
            year, month, day, 1, 30, 0, tzinfo=timezone.utc
        ) + timedelta(days=1)

    window_minutes = get_time_window_minutes(trip_context.departure_time_window)
    if not window_minutes:
        return default_departure
    start_min, end_min = window_minutes
    if end_min < start_min:
        # Defensive (should only happen for late_night, already handled)
        end_min += 24 * 60
    midpoint = (start_min + end_min) / 2.0
    midpoint_rounded = _round_to_half_hour_minutes(midpoint)
    day_offset = 0
    if midpoint_rounded >= 24 * 60:
        midpoint_rounded -= 24 * 60
        day_offset = 1
    hour = midpoint_rounded // 60
    minute = midpoint_rounded % 60
    return datetime(year, month, day, int(hour), int(minute), 0, tzinfo=timezone.utc) + timedelta(
        days=day_offset
    )


def _airport_timings_for(airport_code: str | None) -> AirportTimings:
    code = (airport_code or "").strip().upper()
    overrides = _AIRPORT_TIMINGS_OVERRIDES.get(code, {})
    return AirportTimings(**overrides)


def _build_fallback_snapshot(
    trip_context: TripContext, airport_code: str | None
) -> FlightSnapshot:
    """Deterministic fallback when live providers are unavailable."""
    scheduled_departure = _scheduled_departure_from_window(trip_context)

    # Placeholder: +3h for arrival if we had a route
    scheduled_arrival = None
    if trip_context.destination_airport:
        scheduled_arrival = scheduled_departure + timedelta(hours=3)

    return FlightSnapshot(
        scheduled_departure=scheduled_departure,
        scheduled_arrival=scheduled_arrival,
        departure_terminal=None,
        origin_airport_code=(
            trip_context.origin_airport
            if trip_context.input_mode == "route_search"
            else None
        ),
        destination_airport_code=(
            trip_context.destination_airport
            if trip_context.input_mode == "route_search"
            else None
        ),
        airport_timings=_airport_timings_for(airport_code),
    )


def build_flight_snapshot(trip_context: TripContext) -> FlightSnapshot:
    airport_code = trip_context.origin_airport if trip_context.input_mode == "route_search" else None
    try:
        # TODO Week 2: call live flight provider here
        # snapshot = flight_provider.get_snapshot(trip_context)
        # return snapshot
        raise NotImplementedError("Live provider not yet connected")
    except Exception:
        return _build_fallback_snapshot(trip_context, airport_code)
