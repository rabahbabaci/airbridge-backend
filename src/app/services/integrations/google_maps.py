import logging
import math

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


def get_drive_time(origin_address: str, airport_iata: str) -> dict:
    """Get driving duration and distance from origin to airport via Google Directions API."""
    try:
        destination = get_airport_destination(airport_iata)
        url = "https://maps.googleapis.com/maps/api/directions/json"
        params = {
            "origin": origin_address,
            "destination": destination,
            "key": settings.google_maps_api_key,
            "mode": "driving",
        }
        with httpx.Client() as client:
            response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        routes = data.get("routes") or []
        if not routes:
            logger.warning("Google Directions returned no routes for %s -> %s", origin_address, airport_iata)
            return {
                "duration_minutes": 45,
                "duration_text": "~45 mins (estimate)",
                "distance_text": "unknown",
                "source": "fallback",
            }

        leg = (routes[0].get("legs") or [None])[0]
        if not leg:
            logger.warning("Google Directions returned no legs for %s -> %s", origin_address, airport_iata)
            return {
                "duration_minutes": 45,
                "duration_text": "~45 mins (estimate)",
                "distance_text": "unknown",
                "source": "fallback",
            }

        duration_info = leg.get("duration_in_traffic") or leg.get("duration")
        if not duration_info:
            logger.warning("Google Directions leg has no duration for %s -> %s", origin_address, airport_iata)
            return {
                "duration_minutes": 45,
                "duration_text": "~45 mins (estimate)",
                "distance_text": "unknown",
                "source": "fallback",
            }

        duration_seconds = duration_info.get("value", 0)
        duration_minutes = math.ceil(duration_seconds / 60)
        duration_text = duration_info.get("text", "~45 mins (estimate)")
        distance_text = (leg.get("distance") or {}).get("text", "unknown")

        return {
            "duration_minutes": duration_minutes,
            "duration_text": duration_text,
            "distance_text": distance_text,
            "source": "google_maps",
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
            "duration_text": "~45 mins (estimate)",
            "distance_text": "unknown",
            "source": "fallback",
        }
