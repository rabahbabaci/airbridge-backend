import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


def parse_flight(raw: dict) -> dict:
    """Extract a clean dict from one AeroDataBox flight object."""
    departure = raw.get("departure") or {}
    arrival = raw.get("arrival") or {}
    departure_airport = departure.get("airport") or {}
    arrival_airport = arrival.get("airport") or {}
    scheduled_dep = departure.get("scheduledTime") or {}
    scheduled_arr = arrival.get("scheduledTime") or {}
    airline = raw.get("airline") or {}
    aircraft = raw.get("aircraft") or {}

    return {
        "flight_number": raw.get("number"),
        "airline_name": airline.get("name"),
        "origin_iata": departure_airport.get("iata"),
        "origin_name": departure_airport.get("name"),
        "destination_iata": arrival_airport.get("iata"),
        "destination_name": arrival_airport.get("name"),
        "departure_time_local": scheduled_dep.get("local"),
        "departure_time_utc": scheduled_dep.get("utc"),
        "arrival_time_local": scheduled_arr.get("local"),
        "arrival_time_utc": scheduled_arr.get("utc"),
        "departure_terminal": departure.get("terminal"),
        "departure_gate": departure.get("gate"),
        "arrival_terminal": arrival.get("terminal"),
        "status": raw.get("status", "Unknown"),
        "aircraft_model": aircraft.get("model"),
    }


def lookup_flights(flight_number: str, date_str: str) -> list[dict]:
    """Call AeroDataBox Flight status (specific date) API and return parsed flights."""
    try:
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
        with httpx.Client() as client:
            response = client.get(url, headers=headers, params=params)
        if response.status_code != 200:
            logger.warning(
                "AeroDataBox API returned status %s for %s on %s",
                response.status_code,
                flight_number,
                date_str,
            )
            return []
        data = response.json()
        if not isinstance(data, list):
            logger.warning("AeroDataBox API returned non-list for %s on %s", flight_number, date_str)
            return []
        return [parse_flight(f) for f in data]
    except Exception as e:
        logger.exception("AeroDataBox lookup failed for %s on %s: %s", flight_number, date_str, e)
        return []
