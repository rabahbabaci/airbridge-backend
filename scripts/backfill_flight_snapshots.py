"""Backfill Trip.flight_info / Trip.flight_status for pre-0012 trips.

For each trip where flight_info IS NULL, tries to reconstruct the frozen snapshot
from denormalized columns (origin_iata/destination_iata/airline/flight_number) plus
projected_timeline.departure_utc or selected_departure_utc — no network call.
Falls back to a single AeroDataBox lookup when the scalars aren't enough.

Idempotent: trips that already have flight_info set are skipped.

Usage:
    PYTHONPATH=src python scripts/backfill_flight_snapshots.py --dry-run
    PYTHONPATH=src python scripts/backfill_flight_snapshots.py
    PYTHONPATH=src python scripts/backfill_flight_snapshots.py --limit 50
"""

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Airport, Trip
from app.services.integrations.aerodatabox import lookup_flights
from app.services.integrations.airport_defaults import AIRPORT_TIMEZONES
from app.services.flight_snapshot_service import (
    _select_flight,
    build_flight_info_and_status,
)

logger = logging.getLogger("backfill_flight_snapshots")


def _make_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_utc_iso(scheduled_dep_iso: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(scheduled_dep_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _derive_local_hour(scheduled_dep_iso: str, origin_iata: str) -> int | None:
    """Derive local hour from UTC ISO departure + AIRPORT_TIMEZONES map.

    Falls back to UTC hour (with a warning log) when the IATA is not in the tz map.
    Returns None when the ISO string can't be parsed.
    """
    dt = _parse_utc_iso(scheduled_dep_iso)
    if dt is None:
        return None
    tz_name = AIRPORT_TIMEZONES.get(origin_iata)
    if tz_name:
        return dt.astimezone(ZoneInfo(tz_name)).hour
    logger.warning(
        "AIRPORT_TIMEZONES missing for %s — falling back to UTC hour for departure_local_hour",
        origin_iata,
    )
    return dt.hour


def _derive_local_time_string(scheduled_dep_iso: str, origin_iata: str) -> str | None:
    """Derive "YYYY-MM-DD HH:MM" local-time string, matching AeroDataBox format.

    Returns None when the origin timezone is unknown (we'd rather skip than
    mis-label; the frontend has a sensible fallback to the UTC string).
    """
    dt = _parse_utc_iso(scheduled_dep_iso)
    if dt is None:
        return None
    tz_name = AIRPORT_TIMEZONES.get(origin_iata)
    if not tz_name:
        logger.warning(
            "AIRPORT_TIMEZONES missing for %s — skipping scheduled_departure_local",
            origin_iata,
        )
        return None
    return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M")


def _reconstruct_from_scalars(
    row: Trip, airport_names: dict[str, str] | None = None
) -> tuple[dict, dict] | None:
    """Build flight_info/flight_status from existing Trip columns without an API call.

    ``airport_names`` is a preloaded iata_code → name map from the Airport table,
    used to fill ``destination_name``. When the destination IATA isn't in the
    map, that field stays None rather than guessed.

    Returns None when scalar data is insufficient (caller should fall back to ADB).
    """
    if not row.flight_number or not row.origin_iata or not row.destination_iata or not row.airline:
        return None

    scheduled_dep = None
    if row.selected_departure_utc:
        scheduled_dep = row.selected_departure_utc
    elif row.projected_timeline and row.projected_timeline.get("departure_utc"):
        scheduled_dep = row.projected_timeline["departure_utc"]
    if not scheduled_dep:
        return None

    snapshot_taken_at = _iso(row.created_at) or datetime.now(tz=timezone.utc).isoformat()
    local_hour = _derive_local_hour(scheduled_dep, row.origin_iata)
    local_time = _derive_local_time_string(scheduled_dep, row.origin_iata)
    destination_name = (airport_names or {}).get(row.destination_iata)

    flight_info = {
        "airline": row.airline,
        "flight_number": row.flight_number,
        "origin_iata": row.origin_iata,
        "destination_iata": row.destination_iata,
        "destination_name": destination_name,
        "scheduled_departure_at": scheduled_dep,
        "scheduled_departure_local": local_time,
        "scheduled_arrival_at": None,
        "aircraft_type": None,
        "terminal": None,
        "duration_minutes": None,
        "departure_local_hour": local_hour,
        "snapshot_taken_at": snapshot_taken_at,
    }
    flight_status = {
        "gate": None,
        "status": None,
        "delay_minutes": 0,
        "actual_departure_at": None,
        "cancelled": False,
        "last_updated_at": snapshot_taken_at,
    }
    return flight_info, flight_status


def _backfill_from_adb(row: Trip) -> tuple[dict, dict] | None:
    """Fetch ADB and build info/status. Overrides snapshot_taken_at to trip.created_at."""
    if not row.flight_number or not row.departure_date:
        return None

    logger.info(
        "ADB call: flight_number=%s departure_date=%s trip_id=%s",
        row.flight_number,
        row.departure_date,
        row.id,
    )
    flights = lookup_flights(row.flight_number, row.departure_date)
    if not flights:
        return None

    flight = _select_flight(flights, row.selected_departure_utc)
    flight_info, flight_status = build_flight_info_and_status(flight)
    if flight_info is None:
        return None

    created_iso = _iso(row.created_at)
    if created_iso:
        flight_info["snapshot_taken_at"] = created_iso
        flight_status["last_updated_at"] = created_iso
    return flight_info, flight_status


async def _load_airport_names(session) -> dict[str, str]:
    """Preload Airport.iata_code → Airport.name for destination_name fill-in."""
    try:
        result = await session.execute(select(Airport.iata_code, Airport.name))
        return {iata: name for iata, name in result.all() if iata and name}
    except Exception:
        logger.exception("Failed to preload airport names; destination_name will be None")
        return {}


async def process_trips(session, dry_run: bool, limit: int | None) -> dict:
    """Backfill loop against an open session. Returns counts dict.

    Factored out from ``run`` so tests can drive it with a test SQLite session.
    """
    reconstructed = 0
    api_called = 0
    skipped = 0
    written = 0

    airport_names = await _load_airport_names(session)

    stmt = select(Trip).where(Trip.flight_info.is_(None))
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    logger.info("Found %d trips with flight_info IS NULL", len(rows))

    for row in rows:
        plan = _reconstruct_from_scalars(row, airport_names=airport_names)
        if plan is not None:
            action = "reconstruct"
            reconstructed += 1
        else:
            action = "api_call"
            if not dry_run:
                plan = _backfill_from_adb(row)
            api_called += 1

        if dry_run:
            logger.info(
                "[dry-run] trip_id=%s plan=%s flight_number=%s departure_date=%s",
                row.id,
                action,
                row.flight_number,
                row.departure_date,
            )
            continue

        if plan is None:
            logger.warning(
                "Skip trip_id=%s — no scalars and ADB returned nothing", row.id
            )
            skipped += 1
            continue

        flight_info, flight_status = plan
        row.flight_info = flight_info
        row.flight_status = flight_status
        # Also fill denormalized scalars if they were null (ADB-backfilled case).
        # flight_info is the source of truth on conflict; only fill missing scalars.
        if row.origin_iata is None:
            row.origin_iata = flight_info.get("origin_iata")
        if row.destination_iata is None:
            row.destination_iata = flight_info.get("destination_iata")
        if row.airline is None:
            row.airline = flight_info.get("airline")
        written += 1

    if not dry_run:
        await session.commit()

    counts = {
        "candidates": len(rows),
        "reconstructed": reconstructed,
        "api_called": api_called,
        "written": written,
        "skipped": skipped,
    }
    logger.info(
        "Done. reconstructed=%d api_call=%d written=%d skipped=%d dry_run=%s",
        reconstructed,
        api_called,
        written,
        skipped,
        dry_run,
    )
    return counts


async def run(dry_run: bool, limit: int | None) -> None:
    if not settings.database_url:
        raise SystemExit("DATABASE_URL not configured.")

    engine = create_async_engine(_make_async_url(settings.database_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await process_trips(session, dry_run=dry_run, limit=limit)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts and per-trip plan without writing or calling ADB.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of trips to process (useful for staged rollouts).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    main()
