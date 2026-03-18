"""Generate TSA baseline matrix from existing TSA_DATA in tsa_estimator.py."""

import json
import math
import os
import sys

from tsa_estimator import TSA_DATA

# Default for airports not in TSA_DATA
DEFAULT_TSA = (8, 15, 25)  # (off_peak, average, peak)

# Hour-to-band mapping
# Peak: 5-9 (morning rush), 15-19 (evening rush)
# Secondary peak: 11-13 (midday surge)
# Off-peak: 20-4
# Average: 10, 14
PEAK_HOURS = {5, 6, 7, 8, 9, 15, 16, 17, 18, 19}
SECONDARY_PEAK_HOURS = {11, 12, 13}
OFF_PEAK_HOURS = {20, 21, 22, 23, 0, 1, 2, 3, 4}
AVERAGE_HOURS = {10, 14}

# Day-of-week multipliers (0=Monday)
DAY_MULTIPLIERS = {
    0: 1.0,   # Monday
    1: 1.0,   # Tuesday
    2: 1.0,   # Wednesday
    3: 1.0,   # Thursday
    4: 1.1,   # Friday
    5: 0.7,   # Saturday
    6: 0.85,  # Sunday
}


def generate_airport_matrix(off_peak: int, average: int, peak: int) -> dict:
    secondary_peak = round((average + peak) / 2)
    matrix = {}
    for dow in range(7):
        day_key = str(dow)
        matrix[day_key] = {}
        multiplier = DAY_MULTIPLIERS[dow]
        for hour in range(24):
            if hour in PEAK_HOURS:
                base = peak
            elif hour in SECONDARY_PEAK_HOURS:
                base = secondary_peak
            elif hour in OFF_PEAK_HOURS:
                base = off_peak
            else:
                base = average
            p50 = round(base * multiplier)
            p25 = max(3, round(p50 * 0.65))
            p75 = round(p50 * 1.4)
            p80 = round(p50 * 1.55)
            matrix[day_key][str(hour)] = {
                "p25": p25,
                "p50": p50,
                "p75": p75,
                "p80": p80,
            }
    return matrix


def main():
    baselines = {}

    # Generate for all airports in TSA_DATA
    for airport, (off_peak, average, peak) in sorted(TSA_DATA.items()):
        baselines[airport] = generate_airport_matrix(off_peak, average, peak)

    # Generate DEFAULT entry
    baselines["DEFAULT"] = generate_airport_matrix(*DEFAULT_TSA)

    output_path = os.path.join(
        os.path.dirname(__file__), "..", "src", "app", "data", "tsa_baselines.json"
    )
    output_path = os.path.normpath(output_path)

    with open(output_path, "w") as f:
        json.dump(baselines, f, indent=2)

    print(f"Generated TSA baselines for {len(baselines)} airports -> {output_path}")


if __name__ == "__main__":
    main()
