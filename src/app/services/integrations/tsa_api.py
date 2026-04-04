"""TSAWaitTimes.com API client with in-memory cache."""

import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 900  # 15 minutes


async def fetch_live_tsa_wait(airport_iata: str) -> dict | None:
    """Fetch live TSA wait from TSAWaitTimes.com. Returns dict or None on failure."""
    if not settings.tsa_wait_times_api_key:
        return None

    cache_key = (airport_iata or "").upper()
    now = time.time()

    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return data

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"https://api.tsawaittimes.com/api/airport/{cache_key}/json",
                headers={"x-api-key": settings.tsa_wait_times_api_key},
            )
            resp.raise_for_status()
            raw = resp.json()
            result = {
                "wait_minutes": raw.get("estimated_wait", 0),
                "fetched_at": now,
            }
            _cache[cache_key] = (now, result)
            return result
    except Exception:
        logger.debug("TSA API request failed for %s", cache_key, exc_info=True)
        return None


def clear_cache() -> None:
    """Clear the in-memory cache (useful for testing)."""
    _cache.clear()
