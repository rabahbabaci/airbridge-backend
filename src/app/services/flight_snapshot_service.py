"""Build flight snapshot from trip context. Uses AeroDataBox for live flight data."""

from datetime import datetime, timezone, timedelta

from app.schemas.flight_snapshot import AirportTimings, FlightSnapshot
from app.schemas.trips import TripContext, DepartureTimeWindow
from app.services.integrations.aerodatabox import lookup_flights
from app.services.integrations.airport_defaults import get_airport_timings

TIME_WINDOW_MINUTES: dict[DepartureTimeWindow, tuple[int, int] | None] = {
    DepartureTimeWindow.morning: (6 * 60, 11 * 60 + 59),
    DepartureTimeWindow.midday: (12 * 60, 14 * 60 + 59),
    DepartureTimeWindow.afternoon: (15 * 60, 17 * 60 + 59),
    DepartureTimeWindow.evening: (18 * 60, 21 * 60 + 59),
    # late night wraps past midnight; handle on use (end < start)
    DepartureTimeWindow.late_night: (22 * 60, 5 * 60 + 59),
    DepartureTimeWindow.not_sure: None,
}


def get_time_window_minutes(
    window: DepartureTimeWindow | None,
) -> tuple[int, int] | None:
    if window is None:
        return None
    return TIME_WINDOW_MINUTES.get(window)


def get_available_flights(flight_number: str, date_str: str) -> list[dict]:
    """Return list of flight options from AeroDataBox for the given flight number and date."""
    return lookup_flights(flight_number, date_str)


def _airport_timings_for(airport_code: str | None) -> AirportTimings:
    code = (airport_code or "").strip().upper()
    defaults = get_airport_timings(code)
    return AirportTimings(
        curb_to_checkin_minutes=defaults["curb_to_checkin"],
        parking_to_terminal_minutes=defaults["parking_to_terminal"],
        transit_station_to_terminal_minutes=defaults["transit_to_terminal"],
        checkin_to_security_minutes=defaults["checkin_to_security"],
        security_minutes=25,  # placeholder — TSA estimate injected in recommendation service
        security_to_gate_minutes=defaults["security_to_gate"],
    )


def _parse_utc_datetime(s: str | None) -> datetime | None:
    """Parse AeroDataBox UTC string (e.g. '2026-03-07 18:09Z') to timezone-aware datetime."""
    if not s:
        return None
    try:
        normalized = (s or "").strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def _build_fallback_snapshot(
    trip_context: TripContext, airport_code: str | None
) -> FlightSnapshot:
    """Deterministic fallback when live providers are unavailable."""
    # Placeholder departure time. In Week 2:
    # - flight_number mode: replaced by exact time from flight API lookup
    # - route_search mode: replaced by exact time after user selects a flight
    #   (departure_time_window is a search filter for the flight API, not a departure time)
    year = trip_context.departure_date.year
    month = trip_context.departure_date.month
    day = trip_context.departure_date.day
    scheduled_departure = datetime(year, month, day, 10, 0, 0, tzinfo=timezone.utc)

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
    airport_code = (
        trip_context.origin_airport if trip_context.input_mode == "route_search" else None
    )
    try:
        if trip_context.flight_number and trip_context.departure_date:
            flights = lookup_flights(
                trip_context.flight_number, str(trip_context.departure_date)
            )
            if flights:
                selected_utc = (trip_context.selected_departure_utc or "").strip()
                if selected_utc:
                    flight = next(
                        (
                            f
                            for f in flights
                            if (f.get("departure_time_utc") or "").strip() == selected_utc
                        ),
                        flights[0],
                    )
                else:
                    flight = flights[0]
                origin_iata = flight.get("origin_iata")
                scheduled_departure = _parse_utc_datetime(flight.get("departure_time_utc"))
                scheduled_arrival = _parse_utc_datetime(flight.get("arrival_time_utc"))
                if scheduled_departure is not None:
                    return FlightSnapshot(
                        scheduled_departure=scheduled_departure,
                        scheduled_arrival=scheduled_arrival,
                        departure_terminal=flight.get("departure_terminal"),
                        origin_airport_code=origin_iata,
                        destination_airport_code=flight.get("destination_iata"),
                        airport_timings=_airport_timings_for(origin_iata),
                    )
        return _build_fallback_snapshot(trip_context, airport_code)
    except Exception:
        return _build_fallback_snapshot(trip_context, airport_code)
