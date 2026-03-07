"""Per-airport walking time defaults for journey segments (researched)."""

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


def get_airport_timings(airport_iata: str) -> dict[str, int]:
    """Return walking-time defaults for the given airport, or the default for unknown airports."""
    return AIRPORT_TIMINGS.get(
        (airport_iata or "").upper(),
        DEFAULT_TIMINGS.copy(),
    )
