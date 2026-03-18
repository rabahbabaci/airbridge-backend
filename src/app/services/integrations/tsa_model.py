"""TSA wait time model using baseline percentiles, security access discounts, and volume adjustments."""

import json
from pathlib import Path

_baselines: dict | None = None

SECURITY_ACCESS_MULTIPLIERS: dict[str, float] = {
    "none": 1.0,
    "precheck": 0.35,
    "clear": 0.70,
    "clear_precheck": 0.20,
    "priority_lane": 0.50,
}


def _load_baselines() -> dict:
    global _baselines
    if _baselines is None:
        path = Path(__file__).resolve().parent.parent.parent / "data" / "tsa_baselines.json"
        with open(path) as f:
            _baselines = json.load(f)
    return _baselines


def estimate_tsa_wait(
    airport_iata: str,
    departure_hour: int,
    day_of_week: int | None = None,
    security_access: str = "none",
    flight_volume_ratio: float | None = None,
) -> dict:
    baselines = _load_baselines()

    airport_key = (airport_iata or "").upper()
    if airport_key not in baselines:
        airport_key = "DEFAULT"

    if day_of_week is None:
        day_of_week = 2  # Wednesday — median weekday

    cell = baselines[airport_key][str(day_of_week)][str(departure_hour)]
    p25 = cell["p25"]
    p50 = cell["p50"]
    p75 = cell["p75"]
    p80 = cell["p80"]

    # Apply flight volume ratio
    if flight_volume_ratio is not None:
        ratio = max(0.7, min(2.0, flight_volume_ratio))
        p25 = p25 * ratio
        p50 = p50 * ratio
        p75 = p75 * ratio
        p80 = p80 * ratio

    # Apply security access discount
    discount = SECURITY_ACCESS_MULTIPLIERS.get(security_access, 1.0)
    p25 = round(p25 * discount)
    p50 = round(p50 * discount)
    p75 = round(p75 * discount)
    p80 = round(p80 * discount)

    # Clamp p25 floor
    p25 = max(3, p25)

    return {
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "p80": p80,
        "airport": (airport_iata or "").upper(),
        "source": "baseline",
        "volume_ratio": flight_volume_ratio,
    }
