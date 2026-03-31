"""Per-airport walking time defaults for journey segments (researched)."""

from app.services.integrations.airport_cache import get_cached_airport

AIRPORT_TIMINGS: dict[str, dict[str, int]] = {
    "SFO": {"curb_to_checkin": 5, "checkin_to_security": 3, "security_to_gate": 12, "parking_to_terminal": 12, "transit_to_terminal": 15},
    "OAK": {"curb_to_checkin": 3, "checkin_to_security": 2, "security_to_gate": 8, "parking_to_terminal": 8, "transit_to_terminal": 10},
    "SJC": {"curb_to_checkin": 3, "checkin_to_security": 2, "security_to_gate": 8, "parking_to_terminal": 8, "transit_to_terminal": 12},
    "LAX": {"curb_to_checkin": 7, "checkin_to_security": 5, "security_to_gate": 15, "parking_to_terminal": 15, "transit_to_terminal": 20},
    "JFK": {"curb_to_checkin": 7, "checkin_to_security": 5, "security_to_gate": 15, "parking_to_terminal": 12, "transit_to_terminal": 18},
    "ORD": {"curb_to_checkin": 5, "checkin_to_security": 4, "security_to_gate": 12, "parking_to_terminal": 12, "transit_to_terminal": 15},
    "EWR": {"curb_to_checkin": 5, "checkin_to_security": 4, "security_to_gate": 12, "parking_to_terminal": 10, "transit_to_terminal": 15},
    "ATL": {"curb_to_checkin": 5, "checkin_to_security": 3, "security_to_gate": 15, "parking_to_terminal": 10, "transit_to_terminal": 12},
    "DFW": {"curb_to_checkin": 5, "checkin_to_security": 3, "security_to_gate": 12, "parking_to_terminal": 12, "transit_to_terminal": 10},
    "SEA": {"curb_to_checkin": 5, "checkin_to_security": 3, "security_to_gate": 12, "parking_to_terminal": 10, "transit_to_terminal": 15},
    "DEN": {"curb_to_checkin": 5, "checkin_to_security": 4, "security_to_gate": 15, "parking_to_terminal": 12, "transit_to_terminal": 15},
    "MIA": {"curb_to_checkin": 5, "checkin_to_security": 4, "security_to_gate": 12, "parking_to_terminal": 12, "transit_to_terminal": 15},
    "BOS": {"curb_to_checkin": 5, "checkin_to_security": 3, "security_to_gate": 10, "parking_to_terminal": 10, "transit_to_terminal": 12},
    "SAN": {"curb_to_checkin": 3, "checkin_to_security": 2, "security_to_gate": 8, "parking_to_terminal": 8, "transit_to_terminal": 10},
    "SNA": {"curb_to_checkin": 3, "checkin_to_security": 2, "security_to_gate": 7, "parking_to_terminal": 7, "transit_to_terminal": 10},
    "STS": {"curb_to_checkin": 2, "checkin_to_security": 2, "security_to_gate": 5, "parking_to_terminal": 5, "transit_to_terminal": 8},
}

DEFAULT_TIMINGS: dict[str, int] = {
    "curb_to_checkin": 5,
    "checkin_to_security": 3,
    "security_to_gate": 10,
    "parking_to_terminal": 10,
    "transit_to_terminal": 12,
}

SIZE_CATEGORY_TIMINGS: dict[str, dict[str, int]] = {
    "hub": {"curb_to_checkin": 6, "checkin_to_security": 4, "security_to_gate": 13, "parking_to_terminal": 12, "transit_to_terminal": 15},
    "large": {"curb_to_checkin": 6, "checkin_to_security": 4, "security_to_gate": 13, "parking_to_terminal": 12, "transit_to_terminal": 15},
    "medium": {"curb_to_checkin": 4, "checkin_to_security": 2, "security_to_gate": 8, "parking_to_terminal": 8, "transit_to_terminal": 10},
}

_TIMING_KEYS = ("curb_to_checkin", "checkin_to_security", "security_to_gate", "parking_to_terminal", "transit_to_terminal")


def get_airport_timings(airport_iata: str) -> dict[str, int]:
    """Return walking-time defaults for the given airport.

    Fallback chain:
    1. DB cache (if airport has non-null walking times)
    2. Hardcoded AIRPORT_TIMINGS dict
    3. Size-category generic defaults (from DB cache size_category)
    4. DEFAULT_TIMINGS for completely unknown airports
    """
    code = (airport_iata or "").upper()

    # 1. Check DB cache for populated walking times
    cached = get_cached_airport(code)
    if cached and cached.get("curb_to_checkin") is not None:
        return {k: cached[k] for k in _TIMING_KEYS}

    # 2. Hardcoded researched timings
    if code in AIRPORT_TIMINGS:
        return AIRPORT_TIMINGS[code]

    # 3. Size-category defaults from cached airport
    if cached:
        category = cached.get("size_category", "")
        if category in SIZE_CATEGORY_TIMINGS:
            return SIZE_CATEGORY_TIMINGS[category].copy()

    # 4. Generic defaults
    return DEFAULT_TIMINGS.copy()
