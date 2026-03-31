"""In-memory cache of airport data loaded from the DB at app startup."""

import logging

from sqlalchemy import select

logger = logging.getLogger(__name__)

_airport_cache: dict[str, dict] = {}
_cache_loaded: bool = False


async def load_airport_cache() -> None:
    """Load all airports from DB into memory. Call once at app startup."""
    global _airport_cache, _cache_loaded

    from app.db import engine, async_session_factory

    if engine is None or async_session_factory is None:
        logger.warning("Airport cache: no DB configured, using hardcoded fallbacks only")
        _cache_loaded = True
        return

    try:
        from app.db.models import Airport

        async with async_session_factory() as session:
            result = await session.execute(select(Airport))
            rows = result.scalars().all()

        for row in rows:
            _airport_cache[row.iata_code.upper()] = {
                "name": row.name,
                "city": row.city,
                "country": row.country,
                "lat": row.latitude,
                "lng": row.longitude,
                "size_category": row.size_category,
                "capability_tier": row.capability_tier,
                "curb_to_checkin": row.curb_to_checkin,
                "checkin_to_security": row.checkin_to_security,
                "security_to_gate": row.security_to_gate,
                "parking_to_terminal": row.parking_to_terminal,
                "transit_to_terminal": row.transit_to_terminal,
            }

        _cache_loaded = True
        logger.info("Airport cache loaded: %d airports", len(_airport_cache))
    except Exception as e:
        logger.warning("Airport cache failed to load, using hardcoded fallbacks: %s", e)
        _cache_loaded = True


def get_cached_airport(iata: str) -> dict | None:
    """Return cached airport data or None."""
    return _airport_cache.get((iata or "").upper())
