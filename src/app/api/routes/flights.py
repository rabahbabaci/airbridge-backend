from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.services.flight_snapshot_service import get_available_flights
from app.services.integrations.google_maps import get_drive_time
from app.services.integrations.airport_defaults import get_airport_timings
from app.services.integrations.tsa_estimator import estimate_tsa_wait

router = APIRouter(prefix="/flights", tags=["flights"])

# Statuses that mean the flight is gone
GONE_STATUSES = {"departed", "landed", "arrived"}
CANCELED_STATUSES = {"canceled", "cancelled", "diverted"}


def _parse_utc(utc_str: str | None) -> datetime | None:
    if not utc_str:
        return None
    try:
        return datetime.fromisoformat(utc_str.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _estimate_min_journey(home_address: str, origin_iata: str, departure_hour: int, cache: dict) -> int:
    if origin_iata in cache:
        return cache[origin_iata]
    try:
        drive_data = get_drive_time(home_address, origin_iata, transport_mode="driving")
        drive_min = drive_data.get("duration_minutes", 60)
    except Exception:
        drive_min = 60
    timings = get_airport_timings(origin_iata)
    tsa = estimate_tsa_wait(origin_iata, departure_hour)
    total = (
        drive_min
        + timings["curb_to_checkin"]
        + timings["checkin_to_security"]
        + tsa["estimated_minutes"]
        + timings["security_to_gate"]
    )
    cache[origin_iata] = total
    return total


@router.get("/{flight_number}/{date}")
def get_flights(
    flight_number: str,
    date: str,
    home_address: str = Query(default=""),
):
    result = get_available_flights(flight_number, date)
    if not result:
        raise HTTPException(status_code=404, detail="No flights found for this flight number and date")

    now = datetime.now(tz=timezone.utc)
    drive_cache: dict[str, int] = {}

    enriched = []
    for flight in result:
        status = (flight.get("status") or "Unknown").strip().lower()

        # 1. Flight already departed/landed — show but disable
        if status in GONE_STATUSES:
            flight["departed"] = True
            flight["canceled"] = False
            flight["catchable"] = False
            flight["time_warning"] = "This flight has already departed"
            enriched.append(flight)
            continue

        # 2. Flight canceled — show but disable
        if status in CANCELED_STATUSES:
            flight["departed"] = False
            flight["canceled"] = True
            flight["catchable"] = False
            flight["time_warning"] = "This flight has been canceled"
            enriched.append(flight)
            continue

        # 3. Flight still upcoming (Scheduled, Expected, Departing Late, Unknown, etc.)
        dep_utc = _parse_utc(flight.get("departure_time_utc"))
        flight["departed"] = False
        flight["canceled"] = False

        if dep_utc is None:
            flight["catchable"] = True
            flight["time_warning"] = None
            enriched.append(flight)
            continue

        boarding_time = dep_utc - timedelta(minutes=30)
        mins_until_boarding = int((boarding_time - now).total_seconds() / 60)

        if mins_until_boarding <= 0:
            # Boarding has already started based on time, but status says not departed
            # Still show it — maybe delayed and boarding hasn't started yet
            flight["catchable"] = False
            flight["time_warning"] = "Boarding may have already started"
            enriched.append(flight)
            continue

        if home_address.strip():
            origin_iata = flight.get("origin_iata", "")
            dep_hour = dep_utc.hour
            est = _estimate_min_journey(home_address, origin_iata, dep_hour, drive_cache)

            if mins_until_boarding < est:
                flight["catchable"] = False
                flight["time_warning"] = f"~{est} min to gate, only {mins_until_boarding} min until boarding"
            else:
                flight["catchable"] = True
                flight["time_warning"] = None
        else:
            flight["catchable"] = True
            flight["time_warning"] = None

        enriched.append(flight)

    return {"flights": enriched}
