"""TSA wait time model: three-layer weighted blending.

Layer 1 (static baselines) — always available, from tsa_baselines.json
Layer 2 (live API)         — TSAWaitTimes.com, fresh if fetched_at < 30 min ago
Layer 3 (user feedback)    — TsaObservation aggregates, requires >= 10 observations

Weight distribution:
  All 3 layers:       static=0.30, api=0.50, feedback=0.20
  Static + API:       static=0.375, api=0.625
  Static + Feedback:  static=0.80, feedback=0.20
  Static only:        static=1.0
"""

import json
import time
from pathlib import Path

_baselines: dict | None = None

SECURITY_ACCESS_MULTIPLIERS: dict[str, float] = {
    "none": 1.0,
    "precheck": 0.35,
    "clear": 0.70,
    "clear_precheck": 0.20,
    "priority_lane": 0.50,
}

API_FRESHNESS_SECONDS = 1800  # 30 minutes
MIN_FEEDBACK_OBSERVATIONS = 10


def _load_baselines() -> dict:
    global _baselines
    if _baselines is None:
        path = Path(__file__).resolve().parent.parent.parent / "data" / "tsa_baselines.json"
        with open(path) as f:
            _baselines = json.load(f)
    return _baselines


def _compute_weights(
    has_api: bool,
    has_feedback: bool,
) -> tuple[float, float, float]:
    """Return (static_weight, api_weight, feedback_weight)."""
    if has_api and has_feedback:
        return (0.30, 0.50, 0.20)
    if has_api:
        return (0.375, 0.625, 0.0)
    if has_feedback:
        return (0.80, 0.0, 0.20)
    return (1.0, 0.0, 0.0)


def estimate_tsa_wait(
    airport_iata: str,
    departure_hour: int,
    day_of_week: int | None = None,
    security_access: str = "none",
    flight_volume_ratio: float | None = None,
    live_api_data: dict | None = None,
    user_feedback_data: dict | None = None,
) -> dict:
    """Estimate TSA wait using up to three layers of data.

    Parameters
    ----------
    live_api_data : dict | None
        From tsa_api.fetch_live_tsa_wait(). Keys: wait_minutes, fetched_at.
    user_feedback_data : dict | None
        Aggregated user observations. Keys: avg_wait_minutes, observation_count.
    """
    baselines = _load_baselines()

    airport_key = (airport_iata or "").upper()
    if airport_key not in baselines:
        airport_key = "DEFAULT"

    if day_of_week is None:
        day_of_week = 2  # Wednesday — median weekday

    cell = baselines[airport_key][str(day_of_week)][str(departure_hour)]
    base_p25 = cell["p25"]
    base_p50 = cell["p50"]
    base_p75 = cell["p75"]
    base_p80 = cell["p80"]

    # Apply flight volume ratio to baselines
    if flight_volume_ratio is not None:
        ratio = max(0.7, min(2.0, flight_volume_ratio))
        base_p25 *= ratio
        base_p50 *= ratio
        base_p75 *= ratio
        base_p80 *= ratio

    # Determine layer availability
    has_api = (
        live_api_data is not None
        and "wait_minutes" in live_api_data
        and "fetched_at" in live_api_data
        and (time.time() - live_api_data["fetched_at"]) < API_FRESHNESS_SECONDS
    )
    has_feedback = (
        user_feedback_data is not None
        and user_feedback_data.get("observation_count", 0) >= MIN_FEEDBACK_OBSERVATIONS
    )

    w_static, w_api, w_feedback = _compute_weights(has_api, has_feedback)

    # Blend p50
    blended_p50 = w_static * base_p50
    if has_api:
        blended_p50 += w_api * live_api_data["wait_minutes"]
    if has_feedback:
        blended_p50 += w_feedback * user_feedback_data["avg_wait_minutes"]

    # Scale other percentiles proportionally from baseline ratios
    if base_p50 > 0:
        scale = blended_p50 / base_p50
    else:
        scale = 1.0

    p25 = base_p25 * scale
    p50 = blended_p50
    p75 = base_p75 * scale
    p80 = base_p80 * scale

    # Apply security access discount
    discount = SECURITY_ACCESS_MULTIPLIERS.get(security_access, 1.0)
    p25 = round(p25 * discount)
    p50 = round(p50 * discount)
    p75 = round(p75 * discount)
    p80 = round(p80 * discount)

    # Clamp p25 floor
    p25 = max(3, p25)

    # Determine source label
    sources = ["baseline"]
    if has_api:
        sources.append("api")
    if has_feedback:
        sources.append("feedback")

    return {
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "p80": p80,
        "airport": (airport_iata or "").upper(),
        "source": "+".join(sources),
        "volume_ratio": flight_volume_ratio,
    }
