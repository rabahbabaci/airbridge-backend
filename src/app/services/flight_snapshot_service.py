"""Build flight snapshot from trip context. Uses AeroDataBox for live flight data."""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

from app.schemas.flight_snapshot import FlightSnapshot
from app.schemas.trips import TripContext
from app.services.integrations.aerodatabox import AeroDataBoxError, lookup_flights

# In-memory cache: "flight_number|date" -> list of flight dicts.
# Scope after Phase 2: track-time dedup only. The polling agent no longer routes through
# build_flight_snapshot; it reads flight_info/flight_status columns and uses
# refresh_flight_status for live updates. Entries here live until process restart.
_flight_cache: dict[str, list[dict]] = {}
_FLIGHT_CACHE_MAX = 1000


def get_available_flights(flight_number: str, date_str: str) -> list[dict]:
    """Return list of flight options from AeroDataBox for the given flight number and date."""
    return lookup_flights(flight_number, date_str)


def _select_flight(flights: list[dict], selected_utc: str | None) -> dict | None:
    """Pick the flight whose departure_time_utc matches selected_utc; fall back to first."""
    if not flights:
        return None
    selected_utc = (selected_utc or "").strip()
    if selected_utc:
        return next(
            (
                f
                for f in flights
                if (f.get("departure_time_utc") or "").strip() == selected_utc
            ),
            flights[0],
        )
    return flights[0]


def get_selected_flight(
    flight_number: str, date_str: str, selected_utc: str | None
) -> dict | None:
    """Return the single ADB flight dict matching selected_utc.

    Hits the module-level ``_flight_cache`` populated by ``build_flight_snapshot``
    to avoid a redundant AeroDataBox call when track has already computed a
    recommendation. Calls ``lookup_flights`` only on cache miss.
    """
    cache_key = f"{flight_number}|{date_str}"
    flights = _flight_cache.get(cache_key)
    if flights is None:
        flights = lookup_flights(flight_number, date_str)
        if flights:
            if len(_flight_cache) >= _FLIGHT_CACHE_MAX:
                oldest_key = next(iter(_flight_cache))
                del _flight_cache[oldest_key]
            _flight_cache[cache_key] = flights
    return _select_flight(flights, selected_utc)


def _iso_utc(s: str | None) -> str | None:
    """Normalize an ADB UTC string (e.g. '2026-04-10 14:00Z') to ISO 8601 with +00:00."""
    dt = _parse_utc_datetime(s)
    return dt.isoformat() if dt else None


def build_flight_info_and_status(flight: dict | None) -> tuple[dict | None, dict | None]:
    """Convert a raw AeroDataBox flight dict into the (flight_info, flight_status) pair.

    ``flight_info`` is the frozen-at-track-time record (schedule, route, aircraft, terminal).
    ``flight_status`` is the live record (gate, status, delay) updated by the polling agent.

    Returns (None, None) for a None/empty input.
    """
    if not flight:
        return None, None

    now_iso = datetime.now(tz=timezone.utc).isoformat()

    scheduled_dep_dt = _parse_utc_datetime(flight.get("departure_time_utc"))
    scheduled_arr_dt = _parse_utc_datetime(flight.get("arrival_time_utc"))
    duration_minutes = None
    if scheduled_dep_dt and scheduled_arr_dt:
        duration_minutes = int(
            (scheduled_arr_dt - scheduled_dep_dt).total_seconds() // 60
        )

    # departure_local_hour is needed by recommendation_service for TSA bucket selection.
    # Prefer the local-time string from ADB; fall back to UTC hour when local is missing.
    local_hour = _extract_local_hour(flight.get("departure_time_local"))
    if local_hour is None and scheduled_dep_dt is not None:
        local_hour = scheduled_dep_dt.hour

    flight_info = {
        "airline": flight.get("airline_name"),
        "flight_number": flight.get("flight_number"),
        "origin_iata": flight.get("origin_iata"),
        "destination_iata": flight.get("destination_iata"),
        "destination_name": flight.get("destination_name"),
        "scheduled_departure_at": scheduled_dep_dt.isoformat() if scheduled_dep_dt else None,
        "scheduled_departure_local": flight.get("departure_time_local"),
        "scheduled_arrival_at": scheduled_arr_dt.isoformat() if scheduled_arr_dt else None,
        "aircraft_type": flight.get("aircraft_model"),
        "terminal": flight.get("departure_terminal"),
        "duration_minutes": duration_minutes,
        "departure_local_hour": local_hour,
        "snapshot_taken_at": now_iso,
    }

    revised_dt = _parse_utc_datetime(flight.get("revised_departure_utc"))
    delay_minutes = 0
    actual_departure_at = None
    if revised_dt and scheduled_dep_dt and revised_dt > scheduled_dep_dt:
        delay_minutes = int((revised_dt - scheduled_dep_dt).total_seconds() // 60)
        actual_departure_at = revised_dt.isoformat()

    status_value = flight.get("status")
    flight_status = {
        "gate": flight.get("departure_gate"),
        "status": status_value,
        "delay_minutes": delay_minutes,
        "actual_departure_at": actual_departure_at,
        "cancelled": status_value == "Cancelled",
        "last_updated_at": now_iso,
    }

    return flight_info, flight_status


def snapshot_from_columns(
    flight_info: dict | None, flight_status: dict | None
) -> FlightSnapshot | None:
    """Reconstruct the in-memory FlightSnapshot from persisted columns.

    ``flight_info`` supplies frozen fields (schedule, terminal, origin, local hour).
    ``flight_status`` supplies the live ``departure_gate``. Either being None/missing
    returns None; a missing flight_status just means the gate is unknown.

    Returns None when flight_info lacks a parseable scheduled_departure_at or
    origin_iata — the caller should fall back to the fresh-ADB path.
    """
    if not flight_info:
        return None

    scheduled_dep = _parse_utc_datetime(flight_info.get("scheduled_departure_at"))
    if scheduled_dep is None:
        return None

    origin_iata = flight_info.get("origin_iata")
    if not origin_iata:
        return None

    departure_gate = None
    if flight_status and isinstance(flight_status, dict):
        departure_gate = flight_status.get("gate")

    return FlightSnapshot(
        scheduled_departure=scheduled_dep,
        departure_terminal=flight_info.get("terminal"),
        departure_gate=departure_gate,
        origin_airport_code=origin_iata,
        departure_local_hour=flight_info.get("departure_local_hour"),
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


def build_flight_snapshot(
    trip_context: TripContext, *, strict: bool = False
) -> FlightSnapshot:
    """Build a FlightSnapshot from the trip context.

    ``strict`` controls behavior when the AeroDataBox lookup fails:
    * strict=False (default): swallow ``AeroDataBoxError`` and return a
      deterministic fallback snapshot. Safe for background/polling paths
      where we must not crash the caller.
    * strict=True: re-raise ``AeroDataBoxError`` so the caller (a user-
      initiated recommendation route) can translate it to an HTTP 503.
      Prevents showing the user a lying 10 AM UTC fallback recommendation
      during an upstream outage.

    Hybrid exception handling: genuinely unexpected exceptions (parse
    crashes, downstream code bugs, etc.) still fall back in *both* modes.
    ``strict`` only controls typed ``AeroDataBoxError`` subclasses.
    Leave this hybrid intact — narrowing strict mode to all-exceptions
    has been considered and rejected because parse-path crashes are
    orthogonal to upstream availability and should degrade gracefully
    regardless of mode.
    """
    airport_code = (
        trip_context.origin_airport if trip_context.input_mode == "route_search" else None
    )
    try:
        if trip_context.flight_number and trip_context.departure_date:
            cache_key = f"{trip_context.flight_number}|{trip_context.departure_date}"
            if cache_key in _flight_cache:
                flights = _flight_cache[cache_key]
            else:
                try:
                    flights = lookup_flights(
                        trip_context.flight_number, str(trip_context.departure_date)
                    )
                except AeroDataBoxError as e:
                    if strict:
                        raise
                    logger.warning(
                        "build_flight_snapshot fell back due to %s",
                        type(e).__name__,
                    )
                    return _build_fallback_snapshot(trip_context, airport_code)
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
    except AeroDataBoxError:
        # strict=True re-raised from the inner handler above. Escape the
        # outer except Exception so the route handler can translate to 503.
        raise
    except Exception as e:
        logger.debug("flight_snapshot error: %s", e)
        return _build_fallback_snapshot(trip_context, airport_code)
