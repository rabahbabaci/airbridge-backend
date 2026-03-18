"""TSA wait time estimates from historical data by airport and time of day."""

# (off_peak, average, peak) minutes
TSA_DATA: dict[str, tuple[int, int, int]] = {
    "SFO": (15, 25, 45),
    "OAK": (10, 15, 25),
    "SJC": (10, 15, 25),
    "LAX": (15, 25, 45),
    "JFK": (15, 20, 40),
    "ORD": (10, 15, 35),
    "EWR": (15, 25, 45),
    "ATL": (10, 18, 35),
    "DFW": (10, 15, 30),
    "SEA": (10, 18, 35),
    "DEN": (10, 18, 35),
    "MIA": (15, 22, 40),
    "BOS": (12, 20, 35),
    "SAN": (10, 15, 25),
    "SNA": (8, 12, 20),
    "STS": (5, 8, 12),
}
DEFAULT_TSA = (12, 20, 35)


def estimate_tsa_wait(airport_iata: str, departure_hour_local: int) -> dict:
    """Return TSA wait estimate for airport at given local hour (0-23)."""
    off_peak, average, peak = TSA_DATA.get(
        (airport_iata or "").upper(), DEFAULT_TSA
    )

    if departure_hour_local in (5, 6, 7, 8, 15, 16, 17):
        period = "peak"
        estimated_minutes = peak
    elif departure_hour_local in (9, 10, 11, 12, 13, 14, 20, 21, 22, 23):
        period = "off_peak"
        estimated_minutes = off_peak
    else:
        period = "average"
        estimated_minutes = average

    return {
        "estimated_minutes": estimated_minutes,
        "period": period,
        "airport": (airport_iata or "").upper(),
        "source": "historical_model",
    }
