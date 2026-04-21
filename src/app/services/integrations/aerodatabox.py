import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class AeroDataBoxError(Exception):
    """Base class for AeroDataBox integration failures."""


class AeroDataBoxNotFound(AeroDataBoxError):
    """HTTP 404 — flight/route genuinely doesn't exist in ADB."""


class AeroDataBoxRateLimited(AeroDataBoxError):
    """HTTP 429 — RapidAPI rate limit hit."""


class AeroDataBoxUnavailable(AeroDataBoxError):
    """HTTP 5xx, connection error, or malformed response."""


class AeroDataBoxTimeout(AeroDataBoxError):
    """Request or connection timed out."""


def _classify_status(status_code: int) -> type[AeroDataBoxError]:
    if status_code == 404:
        return AeroDataBoxNotFound
    if status_code == 429:
        return AeroDataBoxRateLimited
    return AeroDataBoxUnavailable


def parse_flight(raw: dict) -> dict:
    """Extract a clean dict from one AeroDataBox flight object."""
    departure = raw.get("departure") or {}
    arrival = raw.get("arrival") or {}
    departure_airport = departure.get("airport") or {}
    arrival_airport = arrival.get("airport") or {}
    scheduled_dep = departure.get("scheduledTime") or {}
    revised_dep = departure.get("revisedTime") or {}
    scheduled_arr = arrival.get("scheduledTime") or {}
    revised_arr = arrival.get("revisedTime") or {}
    airline = raw.get("airline") or {}
    aircraft = raw.get("aircraft") or {}

    # Primary times are always SCHEDULED (what the ticket says)
    dep_utc = scheduled_dep.get("utc")
    dep_local = scheduled_dep.get("local")
    arr_utc = scheduled_arr.get("utc")
    arr_local = scheduled_arr.get("local")

    # Revised times for delays
    revised_dep_utc = revised_dep.get("utc")
    revised_dep_local = revised_dep.get("local")
    is_delayed = bool(revised_dep_utc and dep_utc and revised_dep_utc > dep_utc)

    status = raw.get("status", "Unknown")

    # Normalize local times to "YYYY-MM-DD HH:MM" (replace T, truncate to 16 chars)
    dep_local = dep_local.replace("T", " ")[:16] if dep_local else None
    arr_local = arr_local.replace("T", " ")[:16] if arr_local else None
    revised_dep_local = revised_dep_local.replace("T", " ")[:16] if revised_dep_local else None

    return {
        "flight_number": raw.get("number"),
        "airline_name": airline.get("name"),
        "origin_iata": departure_airport.get("iata"),
        "origin_name": departure_airport.get("name"),
        "destination_iata": arrival_airport.get("iata"),
        "destination_name": arrival_airport.get("name"),
        "departure_time_local": dep_local,
        "departure_time_utc": dep_utc,
        "arrival_time_local": arr_local,
        "arrival_time_utc": arr_utc,
        "revised_departure_local": revised_dep_local,
        "revised_departure_utc": revised_dep_utc,
        "departure_terminal": departure.get("terminal"),
        "departure_gate": departure.get("gate"),
        "arrival_terminal": arrival.get("terminal"),
        "status": status,
        "is_delayed": is_delayed,
        "aircraft_model": aircraft.get("model"),
    }


def parse_departure(raw: dict, origin_iata: str) -> dict | None:
    """Extract a clean dict from one AeroDataBox FIDS departure object.

    The FIDS format uses ``movement`` (destination airport + departure times)
    instead of the ``departure``/``arrival`` structure that ``parse_flight`` expects.
    The origin airport is the airport we queried — it's not in the response.
    Returns None if the destination has no IATA code (unusable for filtering).
    """
    movement = raw.get("movement") or {}
    dest_airport = movement.get("airport") or {}
    dest_iata = dest_airport.get("iata")
    if not dest_iata:
        return None  # skip flights without destination IATA

    scheduled = movement.get("scheduledTime") or {}
    revised = movement.get("revisedTime") or {}
    airline = raw.get("airline") or {}
    aircraft = raw.get("aircraft") or {}

    dep_utc = scheduled.get("utc")
    dep_local = scheduled.get("local")
    revised_dep_utc = revised.get("utc")
    revised_dep_local = revised.get("local")
    is_delayed = bool(revised_dep_utc and dep_utc and revised_dep_utc > dep_utc)

    status = raw.get("status", "Unknown")

    dep_local = dep_local.replace("T", " ")[:16] if dep_local else None
    revised_dep_local = revised_dep_local.replace("T", " ")[:16] if revised_dep_local else None

    return {
        "flight_number": raw.get("number"),
        "airline_name": airline.get("name"),
        "origin_iata": origin_iata,
        "origin_name": None,
        "destination_iata": dest_iata,
        "destination_name": dest_airport.get("name"),
        "departure_time_local": dep_local,
        "departure_time_utc": dep_utc,
        "arrival_time_local": None,
        "arrival_time_utc": None,
        "revised_departure_local": revised_dep_local,
        "revised_departure_utc": revised_dep_utc,
        "departure_terminal": movement.get("terminal"),
        "departure_gate": movement.get("gate"),
        "arrival_terminal": None,
        "status": status,
        "is_delayed": is_delayed,
        "aircraft_model": aircraft.get("model"),
    }


def _fetch_departures_window(iata: str, from_local: str, to_local: str) -> list[dict]:
    """Fetch a single ≤12-hour window of departures. Returns raw dicts.

    Raises the appropriate AeroDataBoxError subclass on any failure path;
    the caller is responsible for partial-success aggregation across windows.
    """
    url = f"https://aerodatabox.p.rapidapi.com/flights/airports/iata/{iata}/{from_local}/{to_local}"
    headers = {
        "x-rapidapi-host": "aerodatabox.p.rapidapi.com",
        "x-rapidapi-key": settings.rapidapi_key,
    }
    params = {
        "withAircraftImage": "false",
        "withLocation": "false",
        "direction": "Departure",
    }
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(url, headers=headers, params=params)
    except httpx.TimeoutException as e:
        raise AeroDataBoxTimeout(
            f"timeout fetching departures for {iata} {from_local}-{to_local}"
        ) from e
    except httpx.HTTPError as e:
        raise AeroDataBoxUnavailable(
            f"connection error fetching departures for {iata} {from_local}-{to_local}"
        ) from e

    # AeroDataBox returns HTTP 204 "No Content" for airport/date combinations
    # with no matching departures (confirmed via cache-control: public,max-age=30
    # on the upstream response — stable, not transient). 204 is a success response
    # per RFC 7231; treat it as an empty list, not an error. Without this, 204
    # would fall through to _classify_status and be misclassified as Unavailable.
    if response.status_code == 204:
        return []

    if response.status_code != 200:
        exc_cls = _classify_status(response.status_code)
        raise exc_cls(
            f"AeroDataBox departures status {response.status_code} for {iata} {from_local}-{to_local}"
        )

    try:
        data = response.json()
    except (ValueError, TypeError) as e:
        raise AeroDataBoxUnavailable(
            f"malformed JSON from departures for {iata} {from_local}-{to_local}"
        ) from e

    departures = data.get("departures", []) if isinstance(data, dict) else []
    if not isinstance(departures, list):
        return []
    return departures


# Severity ranking used when both departure windows fail — the "worst" error
# is re-raised so the route handler maps to the most severe status (503
# vs 404 for rate-limited vs unavailable, etc.).
_ADB_SEVERITY: dict[type[AeroDataBoxError], int] = {
    AeroDataBoxUnavailable: 4,
    AeroDataBoxTimeout: 3,
    AeroDataBoxRateLimited: 2,
    AeroDataBoxNotFound: 1,
}


def lookup_airport_departures(iata: str, date_str: str) -> list[dict]:
    """Call AeroDataBox FIDS/Departures endpoint for all departures from an airport on a date.

    Splits into two ≤12-hour windows to stay within the API's 12-hour limit.
    Partial-success semantics preserved: if one window succeeds and the other
    raises, return the successful window's results and swallow the error with
    a warning log. Only raises when BOTH windows fail — re-raises the worst
    exception by severity.
    """
    iata = iata.strip().upper()
    raw_departures: list[dict] = []
    window_errors: list[AeroDataBoxError] = []

    for from_time, to_time in [("T00:00", "T11:59"), ("T12:00", "T23:59")]:
        try:
            window = _fetch_departures_window(
                iata, f"{date_str}{from_time}", f"{date_str}{to_time}"
            )
            raw_departures.extend(window)
        except AeroDataBoxError as e:
            logger.warning(
                "AeroDataBox departures window failed for %s on %s (%s-%s): %s",
                iata, date_str, from_time, to_time, type(e).__name__,
            )
            window_errors.append(e)

    # Both windows failed with nothing recovered → re-raise the worst exception
    if window_errors and not raw_departures:
        worst = max(window_errors, key=lambda e: _ADB_SEVERITY.get(type(e), 0))
        raise worst

    parsed = []
    for raw in raw_departures:
        flight = parse_departure(raw, origin_iata=iata)
        if flight is not None:
            parsed.append(flight)
    return parsed


def lookup_flights(flight_number: str, date_str: str) -> list[dict]:
    """Call AeroDataBox Flight status (specific date) API and return parsed flights.

    Raises the appropriate AeroDataBoxError subclass on any failure path:
        * AeroDataBoxNotFound: upstream returned 404
        * AeroDataBoxRateLimited: upstream returned 429
        * AeroDataBoxUnavailable: upstream returned 5xx / connection error / malformed response
        * AeroDataBoxTimeout: request or connection timed out
    An upstream 200 with an empty list is returned as ``[]`` (no exception) —
    that's a legitimate "no flights for this number/date".
    """
    flight_number = flight_number.strip()
    url = f"https://aerodatabox.p.rapidapi.com/flights/number/{flight_number}/{date_str}"
    headers = {
        "x-rapidapi-host": "aerodatabox.p.rapidapi.com",
        "x-rapidapi-key": settings.rapidapi_key,
    }
    params = {
        "withAircraftImage": "false",
        "withLocation": "false",
        "dateLocalRole": "Departure",
    }
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(url, headers=headers, params=params)
    except httpx.TimeoutException as e:
        raise AeroDataBoxTimeout(
            f"timeout fetching flight {flight_number} on {date_str}"
        ) from e
    except httpx.HTTPError as e:
        raise AeroDataBoxUnavailable(
            f"connection error fetching flight {flight_number} on {date_str}"
        ) from e

    # AeroDataBox returns HTTP 204 "No Content" for flight numbers it doesn't
    # recognize (confirmed via direct RapidAPI curl — stable, cached by their
    # edge). 204 is a success response per RFC 7231; treat it as an empty list,
    # not an error. The route handler at flights.py maps empty list to 404
    # "No flights found", which is the correct user-facing behavior.
    if response.status_code == 204:
        return []

    if response.status_code != 200:
        exc_cls = _classify_status(response.status_code)
        raise exc_cls(
            f"AeroDataBox status {response.status_code} for {flight_number} on {date_str}"
        )

    try:
        data = response.json()
    except (ValueError, TypeError) as e:
        raise AeroDataBoxUnavailable(
            f"malformed JSON for {flight_number} on {date_str}"
        ) from e

    if not isinstance(data, list):
        raise AeroDataBoxUnavailable(
            f"unexpected response shape for {flight_number} on {date_str} (expected list)"
        )

    return [parse_flight(f) for f in data]
