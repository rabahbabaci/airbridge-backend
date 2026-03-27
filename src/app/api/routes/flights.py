from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.services.flight_snapshot_service import get_available_flights
from app.services.integrations.aerodatabox import lookup_airport_departures
from app.services.integrations.google_maps import get_drive_time
from app.services.integrations.airport_defaults import get_airport_timings
from app.services.integrations.tsa_model import estimate_tsa_wait

router = APIRouter(prefix="/flights", tags=["flights"])

DEPARTED_STATUSES = {"departed", "landed", "arrived"}
BOARDING_STATUSES = {"boarding"}
CANCELED_STATUSES = {"canceled", "cancelled", "diverted"}

TIME_WINDOW_RANGES = {
    "morning": (5, 12),
    "afternoon": (12, 18),
    "evening": (18, 22),
    "red_eye": (22, 5),
}


def _parse_utc(utc_str: str | None) -> datetime | None:
    if not utc_str:
        return None
    try:
        return datetime.fromisoformat(utc_str.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _estimate_min_journey(home_address: str, origin_iata: str, departure_hour: int | None, cache: dict) -> int:
    if origin_iata in cache:
        return cache[origin_iata]
    try:
        drive_data = get_drive_time(home_address, origin_iata, transport_mode="driving")
        drive_min = drive_data.get("duration_minutes", 60)
    except Exception:
        drive_min = 60
    timings = get_airport_timings(origin_iata)
    tsa = estimate_tsa_wait(origin_iata, departure_hour or 12)
    total = (
        drive_min
        + timings["curb_to_checkin"]
        + timings["checkin_to_security"]
        + tsa["p50"]
        + timings["security_to_gate"]
    )
    cache[origin_iata] = total
    return total


def enrich_flights(flights: list[dict], home_address: str = "") -> list[dict]:
    """Add departed/canceled/catchable/time_warning flags to a list of parsed flights."""
    now = datetime.now(tz=timezone.utc)
    drive_cache: dict[str, int] = {}
    enriched = []

    for flight in flights:
        status = (flight.get("status") or "Unknown").strip().lower()

        # 1. Flight already departed/landed
        if status in DEPARTED_STATUSES:
            flight["departed"] = True
            flight["canceled"] = False
            flight["catchable"] = False
            flight["is_boarding"] = False
            flight["time_warning"] = "This flight has already departed"
            enriched.append(flight)
            continue

        # 2. Flight currently boarding
        if status in BOARDING_STATUSES:
            flight["departed"] = False
            flight["canceled"] = False
            flight["catchable"] = False
            flight["is_boarding"] = True
            flight["time_warning"] = "This flight is currently boarding"
            enriched.append(flight)
            continue

        # 3. Flight canceled
        if status in CANCELED_STATUSES:
            flight["departed"] = False
            flight["canceled"] = True
            flight["catchable"] = False
            flight["is_boarding"] = False
            flight["time_warning"] = "This flight has been canceled"
            enriched.append(flight)
            continue

        # 4. Flight status says it's still upcoming — verify with actual times
        flight["canceled"] = False
        flight["is_boarding"] = False

        # Use the best available departure time
        scheduled_utc = _parse_utc(flight.get("departure_time_utc"))
        revised_utc = _parse_utc(flight.get("revised_departure_utc"))
        # Use revised time only if it's later than scheduled (actual delay)
        if revised_utc and scheduled_utc and revised_utc > scheduled_utc:
            dep_utc = revised_utc
        else:
            dep_utc = scheduled_utc

        if dep_utc is None:
            flight["departed"] = False
            flight["catchable"] = True
            flight["time_warning"] = None
            enriched.append(flight)
            continue

        # If departure time has passed, treat as departed regardless of status
        if dep_utc <= now:
            flight["departed"] = True
            flight["catchable"] = False
            flight["time_warning"] = "This flight has already departed"
            enriched.append(flight)
            continue

        # Departure is in the future — check if boarding has started
        boarding_time = dep_utc - timedelta(minutes=30)
        mins_until_boarding = int((boarding_time - now).total_seconds() / 60)

        if mins_until_boarding <= 0:
            # Boarding time has passed but flight hasn't departed yet
            flight["departed"] = False
            flight["catchable"] = False
            flight["time_warning"] = "Boarding may have already started"
            enriched.append(flight)
            continue

        # Flight is catchable — check if user has enough time
        if home_address.strip():
            origin_iata = flight.get("origin_iata", "")
            # Use local departure hour for TSA estimates
            dep_local_str = flight.get("revised_departure_local") or flight.get("departure_time_local")
            dep_hour: int | None = None
            if dep_local_str:
                try:
                    dep_hour = datetime.fromisoformat(dep_local_str.strip()).hour
                except (ValueError, TypeError):
                    pass
            if dep_hour is None:
                dep_hour = dep_utc.hour  # fallback to UTC
            est = _estimate_min_journey(home_address, origin_iata, dep_hour, drive_cache)

            if mins_until_boarding < est:
                flight["departed"] = False
                flight["catchable"] = False
                flight["time_warning"] = f"~{est} min to gate, only {mins_until_boarding} min until boarding"
            else:
                flight["departed"] = False
                flight["catchable"] = True
                flight["time_warning"] = None
        else:
            flight["departed"] = False
            flight["catchable"] = True
            flight["time_warning"] = None

        enriched.append(flight)

    return enriched


@router.get("/{flight_number}/{date}")
def get_flights(
    flight_number: str,
    date: str,
    home_address: str = Query(default=""),
):
    result = get_available_flights(flight_number, date)
    if not result:
        raise HTTPException(status_code=404, detail="No flights found for this flight number and date")

    enriched = enrich_flights(result, home_address)
    return {"flights": enriched}


def _extract_local_hour(local_str: str | None) -> int | None:
    """Extract hour from a local time string like '2026-03-07 06:00'."""
    if not local_str:
        return None
    try:
        return datetime.fromisoformat(local_str.strip()).hour
    except (ValueError, TypeError):
        return None


def _matches_time_window(flight: dict, time_window: str) -> bool:
    """Check if a flight's local departure hour falls within the given time window."""
    local_str = flight.get("departure_time_local")
    hour = _extract_local_hour(local_str)
    if hour is None:
        return True  # can't filter without time info, include it

    rng = TIME_WINDOW_RANGES.get(time_window)
    if rng is None:
        return True

    start, end = rng
    if start < end:
        return start <= hour < end
    else:
        # wraps around midnight (red_eye: 22-5)
        return hour >= start or hour < end


def _matches_airline(flight: dict, airline_query: str) -> bool:
    """Match on IATA airline code (from flight_number prefix) or airline name, case-insensitive partial."""
    query = airline_query.strip().lower()
    if not query:
        return True

    # Check airline_name
    airline_name = (flight.get("airline_name") or "").lower()
    if query in airline_name:
        return True

    # Check IATA code prefix of flight_number (e.g. "UA" from "UA300")
    flight_num = (flight.get("flight_number") or "").upper()
    iata_code = ""
    for ch in flight_num:
        if ch.isalpha():
            iata_code += ch
        else:
            break
    if iata_code and query == iata_code.lower():
        return True

    return False


@router.get("/search")
def search_flights(
    origin: str = Query(..., min_length=3, max_length=4),
    destination: str = Query(..., min_length=3, max_length=4),
    date: str = Query(..., min_length=10, max_length=10),
    time_window: str | None = Query(default=None),
    airline: str | None = Query(default=None),
    home_address: str = Query(default=""),
):
    departures = lookup_airport_departures(origin, date)
    if not departures:
        return {"flights": []}

    # Filter by destination
    dest_upper = destination.strip().upper()
    filtered = [
        f for f in departures
        if (f.get("destination_iata") or "").upper() == dest_upper
    ]

    # Filter by time window
    if time_window:
        filtered = [f for f in filtered if _matches_time_window(f, time_window)]

    # Filter by airline
    if airline:
        filtered = [f for f in filtered if _matches_airline(f, airline)]

    if not filtered:
        return {"flights": []}

    enriched = enrich_flights(filtered, home_address)
    return {"flights": enriched}
