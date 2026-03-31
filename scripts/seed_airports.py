"""Seed the airports table from OurAirports CSV data.

Usage:
    DATABASE_URL=<url> PYTHONPATH=src python scripts/seed_airports.py
"""

import asyncio
import csv
import io
import os
import uuid
from datetime import datetime

import httpx
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Airport
from app.services.integrations.airport_defaults import AIRPORT_TIMINGS

OURAIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"

HUB_AIRPORTS = {
    "ATL", "DFW", "DEN", "ORD", "LAX", "JFK", "SFO", "SEA", "MIA", "EWR",
    "BOS", "LHR", "CDG", "FRA", "AMS", "DXB", "SIN", "HND", "NRT", "ICN",
    "HKG", "SYD", "PEK",
}

TIER_1_AIRPORTS = set(AIRPORT_TIMINGS.keys())


def _make_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _classify(iata: str, airport_type: str) -> tuple[str, int]:
    """Return (size_category, capability_tier) for an airport."""
    if iata in TIER_1_AIRPORTS:
        if iata in HUB_AIRPORTS:
            return "hub", 1
        return "large" if airport_type == "large_airport" else "medium", 1
    if iata in HUB_AIRPORTS:
        return "hub", 2
    if airport_type == "large_airport":
        return "large", 3
    return "medium", 4


def _parse_float(val: str) -> float | None:
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is required")
        return

    # Download CSV
    print(f"Downloading airports CSV from {OURAIRPORTS_CSV_URL} ...")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(OURAIRPORTS_CSV_URL)
        resp.raise_for_status()
    csv_text = resp.text
    print(f"Downloaded {len(csv_text):,} bytes")

    # Parse and filter
    reader = csv.DictReader(io.StringIO(csv_text))
    airports = []
    for row in reader:
        airport_type = row.get("type", "")
        if airport_type not in ("large_airport", "medium_airport"):
            continue
        iata = (row.get("iata_code") or "").strip()
        if not iata or iata in ("0", "-"):
            continue

        size_category, capability_tier = _classify(iata, airport_type)

        timings = AIRPORT_TIMINGS.get(iata, {})

        airports.append(
            Airport(
                id=uuid.uuid4(),
                iata_code=iata,
                icao_code=row.get("ident") or None,
                name=row.get("name", ""),
                city=row.get("municipality") or None,
                country=row.get("iso_country") or None,
                latitude=_parse_float(row.get("latitude_deg", "")),
                longitude=_parse_float(row.get("longitude_deg", "")),
                size_category=size_category,
                capability_tier=capability_tier,
                has_live_tsa_feed=False,
                curb_to_checkin=timings.get("curb_to_checkin"),
                checkin_to_security=timings.get("checkin_to_security"),
                security_to_gate=timings.get("security_to_gate"),
                parking_to_terminal=timings.get("parking_to_terminal"),
                transit_to_terminal=timings.get("transit_to_terminal"),
                created_at=datetime.utcnow(),
            )
        )

    print(f"Filtered to {len(airports)} airports (large + medium with IATA codes)")

    # Connect and insert
    async_url = _make_async_url(database_url)
    engine = create_async_engine(async_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Clear existing rows for idempotent re-run
        await session.execute(delete(Airport))
        session.add_all(airports)
        await session.commit()

    await engine.dispose()

    # Summary
    tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for a in airports:
        tier_counts[a.capability_tier] += 1

    print(f"\nInserted {len(airports)} airports:")
    print(f"  Tier 1 (full model, researched):  {tier_counts[1]}")
    print(f"  Tier 2 (hub, good coverage):      {tier_counts[2]}")
    print(f"  Tier 3 (large, basic):            {tier_counts[3]}")
    print(f"  Tier 4 (medium, minimal):         {tier_counts[4]}")


if __name__ == "__main__":
    asyncio.run(main())
