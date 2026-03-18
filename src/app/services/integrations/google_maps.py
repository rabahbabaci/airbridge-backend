import asyncio
import logging
import math
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

AIRPORT_DESTINATIONS: dict[str, str] = {
    "SFO": "San Francisco International Airport",
    "OAK": "Oakland International Airport",
    "SJC": "San Jose International Airport",
    "LAX": "Los Angeles International Airport",
    "JFK": "John F Kennedy International Airport",
    "ORD": "O'Hare International Airport",
    "EWR": "Newark Liberty International Airport",
    "ATL": "Hartsfield-Jackson Atlanta International Airport",
    "DFW": "Dallas Fort Worth International Airport",
    "SEA": "Seattle-Tacoma International Airport",
    "DEN": "Denver International Airport",
    "MIA": "Miami International Airport",
    "BOS": "Boston Logan International Airport",
    "SAN": "San Diego International Airport",
    "SNA": "John Wayne Airport Orange County",
    "STS": "Charles M. Schulz Sonoma County Airport",
}


def get_airport_destination(iata_code: str) -> str:
    """Return Google Maps–friendly airport name for IATA code, or '{code} Airport'."""
    return AIRPORT_DESTINATIONS.get(iata_code.upper() if iata_code else "", f"{iata_code or ''} Airport")


def _travel_label(transport_mode: str, airport_iata: str) -> str:
    """Return segment label for transport mode and airport."""
    labels = {
        "rideshare": f"Ride to {airport_iata}",
        "driving": f"Drive to {airport_iata}",
        "train": f"Train to {airport_iata}",
        "bus": f"Bus to {airport_iata}",
        "other": f"Travel to {airport_iata}",
    }
    return labels.get(transport_mode, f"Travel to {airport_iata}")


async def _fetch_distance_matrix(
    client: httpx.AsyncClient,
    origin: str,
    destination: str,
    departure_time: int,
    traffic_model: str,
) -> int | None:
    """Fetch duration_in_traffic from Google Distance Matrix API. Returns minutes or None."""
    try:
        params = {
            "origins": origin,
            "destinations": destination,
            "key": settings.google_maps_api_key,
            "mode": "driving",
            "departure_time": str(departure_time),
            "traffic_model": traffic_model,
        }
        resp = await client.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows") or []
        if not rows:
            return None
        elements = rows[0].get("elements") or []
        if not elements or elements[0].get("status") != "OK":
            return None
        dur = elements[0].get("duration_in_traffic") or elements[0].get("duration")
        if not dur:
            return None
        return math.ceil(dur.get("value", 0) / 60)
    except Exception:
        return None


def _fetch_traffic_variants(
    origin: str, destination: str, departure_time: int, primary_minutes: int
) -> tuple[int, int]:
    """Return (pessimistic, optimistic) duration in minutes via parallel Distance Matrix calls."""
    async def _run() -> tuple[int | None, int | None]:
        async with httpx.AsyncClient() as client:
            return await asyncio.gather(
                _fetch_distance_matrix(client, origin, destination, departure_time, "pessimistic"),
                _fetch_distance_matrix(client, origin, destination, departure_time, "optimistic"),
            )

    # NOTE: asyncio.run() creates a new event loop. This works because FastAPI runs
    # sync endpoints in a threadpool. If any endpoint becomes async def, this will
    # raise RuntimeError — refactor to use await directly in that case.
    try:
        pessimistic_min, optimistic_min = asyncio.run(_run())
    except Exception:
        pessimistic_min, optimistic_min = None, None

    if pessimistic_min is None:
        pessimistic_min = math.ceil(primary_minutes * 1.3)
    if optimistic_min is None:
        optimistic_min = math.ceil(primary_minutes * 0.85)

    return pessimistic_min, optimistic_min


def get_drive_time(
    origin_address: str,
    airport_iata: str,
    airport_name: str | None = None,
    transport_mode: str = "rideshare",
    departure_time: int | None = None,
    terminal: str | None = None,
) -> dict:
    """Get duration and distance from origin to airport via Google Directions API."""
    try:
        if terminal and (airport_iata or "").upper() in AIRPORT_DESTINATIONS:
            base_name = AIRPORT_DESTINATIONS[(airport_iata or "").upper()]
            destination = f"{base_name} Terminal {terminal} departures"
        else:
            destination = airport_name or get_airport_destination(airport_iata)
        url = "https://maps.googleapis.com/maps/api/directions/json"
        mode = "driving"
        params: dict[str, str] = {
            "origin": origin_address,
            "destination": destination,
            "key": settings.google_maps_api_key,
            "mode": mode,
        }
        if transport_mode == "train":
            params["mode"] = "transit"
            params["transit_mode"] = "rail"
        elif transport_mode == "bus":
            params["mode"] = "transit"
            params["transit_mode"] = "bus"
        else:
            # rideshare, driving, other
            params["mode"] = "driving"

        if departure_time is not None and departure_time > int(time.time()):
            params["departure_time"] = str(departure_time)

        with httpx.Client(timeout=10) as client:
            response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        label = _travel_label(transport_mode, airport_iata or "airport")

        routes = data.get("routes") or []
        if not routes:
            logger.warning("Google Directions returned no routes for %s -> %s", origin_address, airport_iata)
            return {
                "duration_minutes": 45,
                "duration_pessimistic": 45,
                "duration_optimistic": 45,
                "duration_text": "~45 mins (estimate)",
                "distance_text": "unknown",
                "source": "fallback",
                "label": label,
            }

        leg = (routes[0].get("legs") or [None])[0]
        if not leg:
            logger.warning("Google Directions returned no legs for %s -> %s", origin_address, airport_iata)
            return {
                "duration_minutes": 45,
                "duration_pessimistic": 45,
                "duration_optimistic": 45,
                "duration_text": "~45 mins (estimate)",
                "distance_text": "unknown",
                "source": "fallback",
                "label": label,
            }

        duration_info = leg.get("duration_in_traffic") or leg.get("duration")
        if not duration_info:
            logger.warning("Google Directions leg has no duration for %s -> %s", origin_address, airport_iata)
            return {
                "duration_minutes": 45,
                "duration_pessimistic": 45,
                "duration_optimistic": 45,
                "duration_text": "~45 mins (estimate)",
                "distance_text": "unknown",
                "source": "fallback",
                "label": label,
            }

        duration_seconds = duration_info.get("value", 0)
        duration_minutes = math.ceil(duration_seconds / 60)
        duration_text = duration_info.get("text", "~45 mins (estimate)")
        distance_text = (leg.get("distance") or {}).get("text", "unknown")

        # Compute pessimistic/optimistic traffic variants
        is_driving = params.get("mode") == "driving"
        has_departure = departure_time is not None and departure_time > int(time.time())

        if has_departure and is_driving:
            dur_pessimistic, dur_optimistic = _fetch_traffic_variants(
                origin_address, destination, departure_time, duration_minutes
            )
        elif has_departure and not is_driving:
            # Transit: no traffic_model support
            dur_pessimistic = duration_minutes + 10
            dur_optimistic = duration_minutes
        else:
            # No departure_time — no traffic data available
            dur_pessimistic = duration_minutes
            dur_optimistic = duration_minutes

        return {
            "duration_minutes": duration_minutes,
            "duration_pessimistic": dur_pessimistic,
            "duration_optimistic": dur_optimistic,
            "duration_text": duration_text,
            "distance_text": distance_text,
            "source": "google_maps",
            "label": label,
        }
    except Exception as e:
        logger.exception(
            "Google Directions failed for %s -> %s: %s",
            origin_address,
            airport_iata,
            e,
        )
        return {
            "duration_minutes": 45,
            "duration_pessimistic": 45,
            "duration_optimistic": 45,
            "duration_text": "~45 mins (estimate)",
            "distance_text": "unknown",
            "source": "fallback",
            "label": _travel_label(transport_mode, airport_iata or "airport"),
        }
