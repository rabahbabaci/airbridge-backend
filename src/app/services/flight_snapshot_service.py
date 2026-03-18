"""Build flight snapshot from trip context. Uses AeroDataBox for live flight data."""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

from app.schemas.flight_snapshot import FlightSnapshot
from app.schemas.trips import TripContext
from app.services.integrations.aerodatabox import lookup_flights

# In-memory cache: "flight_number|date" -> list of flight dicts
_flight_cache: dict[str, list[dict]] = {}
_FLIGHT_CACHE_MAX = 1000


def get_available_flights(flight_number: str, date_str: str) -> list[dict]:
    """Return list of flight options from AeroDataBox for the given flight number and date."""
    return lookup_flights(flight_number, date_str)


def _parse_utc_datetime(s: str | None) -> datetime | None:
    """Parse AeroDataBox UTC string (e.g. '2026-03-07 18:09Z') to timezone-aware datetime."""
    if not s:
        return None
    try:
        normalized = (s or "").strip().replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def _extract_local_hour(local_str: str | None) -> int | None:
    """Extract the hour from a local time string like '2026-03-07 06:00'."""
    if not local_str:
        return None
    try:
        dt = datetime.fromisoformat(local_str.strip())
        return dt.hour
    except (ValueError, TypeError):
        return None


def _build_fallback_snapshot(
    trip_context: TripContext, airport_code: str | None
) -> FlightSnapshot:
    """Deterministic fallback when live providers are unavailable."""
    year = trip_context.departure_date.year
    month = trip_context.departure_date.month
    day = trip_context.departure_date.day
    scheduled_departure = datetime(year, month, day, 10, 0, 0, tzinfo=timezone.utc)

    return FlightSnapshot(
        scheduled_departure=scheduled_departure,
        departure_terminal=None,
        origin_airport_code=(
            trip_context.origin_airport
            if trip_context.input_mode == "route_search"
            else None
        ),
        departure_local_hour=10,  # fallback assumes 10 AM local
    )


def build_flight_snapshot(trip_context: TripContext) -> FlightSnapshot:
    airport_code = (
        trip_context.origin_airport if trip_context.input_mode == "route_search" else None
    )
    try:
        if trip_context.flight_number and trip_context.departure_date:
            cache_key = f"{trip_context.flight_number}|{trip_context.departure_date}"
            if cache_key in _flight_cache:
                flights = _flight_cache[cache_key]
            else:
                flights = lookup_flights(
                    trip_context.flight_number, str(trip_context.departure_date)
                )
                if flights:
                    if len(_flight_cache) >= _FLIGHT_CACHE_MAX:
                        oldest_key = next(iter(_flight_cache))
                        del _flight_cache[oldest_key]
                    _flight_cache[cache_key] = flights
            if flights:
                selected_utc = (trip_context.selected_departure_utc or "").strip()
                logger.debug("selected_departure_utc: '%s'", selected_utc)
                logger.debug(
                    "available flights: %s",
                    [(f.get("departure_time_utc"), f.get("origin_iata")) for f in flights],
                )
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
                logger.debug(
                    "matched: %s %s -> %s",
                    flight.get("departure_time_utc"),
                    flight.get("origin_iata"),
                    flight.get("destination_iata"),
                )
                origin_iata = flight.get("origin_iata")

                # Use revised departure if the flight is delayed
                scheduled_utc = _parse_utc_datetime(flight.get("departure_time_utc"))
                revised_utc = _parse_utc_datetime(flight.get("revised_departure_utc"))
                if revised_utc and scheduled_utc and revised_utc > scheduled_utc:
                    scheduled_departure = revised_utc
                    # Extract local hour from revised local time if available,
                    # otherwise fall back to the revised UTC hour
                    local_hour = _extract_local_hour(
                        flight.get("revised_departure_local")
                    ) or (revised_utc.hour if revised_utc else None)
                else:
                    scheduled_departure = scheduled_utc
                    local_hour = _extract_local_hour(
                        flight.get("departure_time_local")
                    )

                if scheduled_departure is not None:
                    return FlightSnapshot(
                        scheduled_departure=scheduled_departure,
                        departure_terminal=flight.get("departure_terminal"),
                        departure_gate=flight.get("departure_gate"),
                        origin_airport_code=origin_iata,
                        departure_local_hour=local_hour,
                    )
        return _build_fallback_snapshot(trip_context, airport_code)
    except Exception as e:
        logger.debug("flight_snapshot error: %s", e)
        return _build_fallback_snapshot(trip_context, airport_code)
