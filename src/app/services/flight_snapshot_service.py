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


def get_time_window_minutes(
    window: DepartureTimeWindow | None,
) -> tuple[int, int] | None:
    if window is None:
        return None
    return TIME_WINDOW_MINUTES.get(window)


def build_flight_snapshot(trip_context: TripContext) -> FlightSnapshot:
    """
    Build a flight snapshot from trip context.
    Returns deterministic placeholder data (e.g. departure at 10:00 on trip date, base TSA 40 min).
    """
    # Placeholder: scheduled departure = trip departure date at 10:00 UTC
    year = trip_context.departure_date.year
    month = trip_context.departure_date.month
    day = trip_context.departure_date.day
    scheduled_departure = datetime(year, month, day, 10, 0, 0, tzinfo=timezone.utc)

    departure_window_minutes = get_time_window_minutes(
        trip_context.departure_time_window
    )

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
        airport_timings=AirportTimings(
            base_tsa_minutes=40,
            check_in_buffer_minutes=30,
        ),
    )
