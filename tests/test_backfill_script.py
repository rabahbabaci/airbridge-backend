"""Tests for scripts/backfill_flight_snapshots.py — dry-run, real, and idempotency."""

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.db.models import Trip, User


MOCK_FLIGHT = {
    "flight_number": "UA200",
    "airline_name": "United Airlines",
    "origin_iata": "SFO",
    "destination_iata": "ORD",
    "departure_time_utc": "2026-05-01 16:00Z",
    "departure_time_local": "2026-05-01 09:00",
    "arrival_time_utc": "2026-05-01 22:00Z",
    "arrival_time_local": "2026-05-01 17:00",
    "revised_departure_utc": None,
    "revised_departure_local": None,
    "departure_terminal": "3",
    "departure_gate": "C10",
    "arrival_terminal": "1",
    "status": "Scheduled",
    "is_delayed": False,
    "aircraft_model": "Boeing 737-900",
    "origin_name": None,
    "destination_name": None,
}


def _seed_trip_reconstructable(factory, user_id, trip_id, created_at):
    """Trip with scalars + selected_departure_utc populated (no ADB needed)."""
    async def _do():
        async with factory() as s:
            s.add(User(id=user_id, trip_count=1, subscription_status="none"))
            s.add(Trip(
                id=trip_id,
                user_id=user_id,
                input_mode="flight_number",
                flight_number="UA200",
                origin_iata="SFO",
                destination_iata="ORD",
                airline="United Airlines",
                departure_date="2026-05-01",
                home_address="1 Market St",
                selected_departure_utc="2026-05-01 16:00Z",
                status="active",
                trip_status="active",
                created_at=created_at,
            ))
            await s.commit()
    asyncio.run(_do())


def _seed_trip_needs_api(factory, user_id, trip_id, created_at):
    """Trip with no scalars and no projected_timeline → backfill must hit ADB."""
    async def _do():
        async with factory() as s:
            s.add(User(id=user_id, trip_count=1, subscription_status="none"))
            s.add(Trip(
                id=trip_id,
                user_id=user_id,
                input_mode="flight_number",
                flight_number="UA200",
                departure_date="2026-05-01",
                home_address="1 Market St",
                selected_departure_utc=None,
                status="active",
                trip_status="active",
                created_at=created_at,
            ))
            await s.commit()
    asyncio.run(_do())


def _read_trip(factory, trip_id):
    async def _do():
        async with factory() as s:
            return await s.get(Trip, trip_id)
    return asyncio.run(_do())


def _run_script(factory, dry_run: bool, limit=None) -> dict:
    """Invoke the script's process_trips against the test SQLite factory."""
    from scripts.backfill_flight_snapshots import process_trips

    async def _do():
        async with factory() as session:
            return await process_trips(session, dry_run=dry_run, limit=limit)

    return asyncio.run(_do())


class TestBackfillScript:
    def test_dry_run_reports_without_writing(self, test_session):
        factory, _ = test_session
        trip_id = uuid.uuid4()
        _seed_trip_reconstructable(
            factory, uuid.uuid4(), trip_id, datetime(2026, 4, 1, 12, 0, 0)
        )

        with patch("scripts.backfill_flight_snapshots.lookup_flights") as mock_lookup:
            counts = _run_script(factory, dry_run=True)

        assert counts["candidates"] == 1
        assert counts["reconstructed"] == 1
        assert counts["api_called"] == 0
        assert counts["written"] == 0
        assert mock_lookup.call_count == 0

        row = _read_trip(factory, trip_id)
        assert row.flight_info is None  # dry-run wrote nothing

    def test_real_run_reconstructs_from_scalars(self, test_session):
        factory, _ = test_session
        trip_id = uuid.uuid4()
        created_at = datetime(2026, 4, 1, 12, 0, 0)
        _seed_trip_reconstructable(factory, uuid.uuid4(), trip_id, created_at)

        with patch("scripts.backfill_flight_snapshots.lookup_flights") as mock_lookup:
            counts = _run_script(factory, dry_run=False)

        assert counts["reconstructed"] == 1
        assert counts["written"] == 1
        assert mock_lookup.call_count == 0  # reconstructed without ADB

        row = _read_trip(factory, trip_id)
        assert row.flight_info is not None
        assert row.flight_info["airline"] == "United Airlines"
        assert row.flight_info["origin_iata"] == "SFO"
        assert row.flight_info["destination_iata"] == "ORD"
        assert row.flight_info["scheduled_departure_at"] == "2026-05-01 16:00Z"
        # snapshot_taken_at == trip.created_at
        assert row.flight_info["snapshot_taken_at"].startswith("2026-04-01T12:00:00")

        assert row.flight_status is not None
        assert row.flight_status["delay_minutes"] == 0
        assert row.flight_status["cancelled"] is False

    def test_real_run_falls_back_to_api_when_scalars_missing(self, test_session):
        factory, _ = test_session
        trip_id = uuid.uuid4()
        created_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        _seed_trip_needs_api(factory, uuid.uuid4(), trip_id, created_at)

        with patch(
            "scripts.backfill_flight_snapshots.lookup_flights",
            return_value=[MOCK_FLIGHT],
        ) as mock_lookup:
            counts = _run_script(factory, dry_run=False)

        assert counts["api_called"] == 1
        assert counts["written"] == 1
        assert mock_lookup.call_count == 1

        row = _read_trip(factory, trip_id)
        assert row.flight_info is not None
        assert row.flight_info["scheduled_departure_at"] == "2026-05-01T16:00:00+00:00"
        assert row.flight_info["aircraft_type"] == "Boeing 737-900"
        assert row.flight_info["terminal"] == "3"
        # snapshot_taken_at overridden to trip.created_at, not "now"
        assert row.flight_info["snapshot_taken_at"].startswith("2026-04-01T12:00:00")
        # Denormalized scalars filled in from ADB response
        assert row.origin_iata == "SFO"
        assert row.destination_iata == "ORD"
        assert row.airline == "United Airlines"

    def test_idempotent_rerun_is_noop(self, test_session):
        factory, _ = test_session
        trip_id = uuid.uuid4()
        _seed_trip_reconstructable(
            factory, uuid.uuid4(), trip_id, datetime(2026, 4, 1, 12, 0, 0)
        )

        with patch("scripts.backfill_flight_snapshots.lookup_flights") as mock_lookup:
            first = _run_script(factory, dry_run=False)
            second = _run_script(factory, dry_run=False)

        assert first["written"] == 1
        assert second["candidates"] == 0
        assert second["written"] == 0
        assert mock_lookup.call_count == 0

    def test_backfill_derives_local_hour_from_tz_map(self, test_session):
        """SFO → America/Los_Angeles; 16:00 UTC → 09:00 local (PDT) or 08:00 (PST)."""
        factory, _ = test_session
        trip_id = uuid.uuid4()
        _seed_trip_reconstructable(
            factory, uuid.uuid4(), trip_id, datetime(2026, 4, 1, 12, 0, 0)
        )

        with patch("scripts.backfill_flight_snapshots.lookup_flights"):
            _run_script(factory, dry_run=False)

        row = _read_trip(factory, trip_id)
        # 2026-05-01 is in DST → PDT (UTC-7) → 16:00 UTC == 09:00 local
        assert row.flight_info["departure_local_hour"] == 9
        # Phase 3: also derive the full local-time string
        assert row.flight_info["scheduled_departure_local"] == "2026-05-01 09:00"

    def test_backfill_derives_destination_name_from_airport_table(self, test_session):
        """destination_name is filled from the Airport table by IATA lookup."""
        from app.db.models import Airport

        factory, _ = test_session
        trip_id = uuid.uuid4()
        _seed_trip_reconstructable(
            factory, uuid.uuid4(), trip_id, datetime(2026, 4, 1, 12, 0, 0)
        )

        # Seed Airport table — process_trips preloads iata → name
        async def _seed_airport():
            async with factory() as s:
                s.add(Airport(
                    iata_code="ORD",
                    name="O'Hare International Airport",
                    size_category="hub",
                    capability_tier=1,
                ))
                await s.commit()
        asyncio.run(_seed_airport())

        with patch("scripts.backfill_flight_snapshots.lookup_flights"):
            _run_script(factory, dry_run=False)

        row = _read_trip(factory, trip_id)
        assert row.flight_info["destination_name"] == "O'Hare International Airport"

    def test_backfill_skips_local_time_when_tz_missing(self, test_session, caplog):
        """Unknown IATA → warning + scheduled_departure_local is None."""
        import logging
        from app.db.models import User
        factory, _ = test_session
        trip_id = uuid.uuid4()

        async def _seed():
            async with factory() as s:
                s.add(User(id=uuid.uuid4(), trip_count=1, subscription_status="none"))
                s.add(Trip(
                    id=trip_id,
                    input_mode="flight_number",
                    flight_number="ZZ999",
                    origin_iata="ZZZ",
                    destination_iata="YYY",
                    airline="Unknown Air",
                    departure_date="2026-05-01",
                    home_address="1 Market St",
                    selected_departure_utc="2026-05-01 16:00Z",
                    status="active",
                    trip_status="active",
                    created_at=datetime(2026, 4, 1, 12, 0, 0),
                ))
                await s.commit()

        asyncio.run(_seed())

        with patch("scripts.backfill_flight_snapshots.lookup_flights"), \
                caplog.at_level(logging.WARNING, logger="backfill_flight_snapshots"):
            _run_script(factory, dry_run=False)

        row = _read_trip(factory, trip_id)
        assert row.flight_info["scheduled_departure_local"] is None
        assert any(
            "scheduled_departure_local" in r.message
            and "AIRPORT_TIMEZONES missing" in r.message
            for r in caplog.records
        )

    def test_backfill_falls_back_to_utc_when_tz_map_missing(
        self, test_session, caplog
    ):
        """Unknown IATA → warning log + UTC hour fallback."""
        import logging

        factory, _ = test_session
        trip_id = uuid.uuid4()

        async def _seed():
            async with factory() as s:
                s.add(User(id=uuid.uuid4(), trip_count=1, subscription_status="none"))
                s.add(Trip(
                    id=trip_id,
                    input_mode="flight_number",
                    flight_number="ZZ999",
                    # "ZZZ" is not in AIRPORT_TIMEZONES
                    origin_iata="ZZZ",
                    destination_iata="YYY",
                    airline="Unknown Air",
                    departure_date="2026-05-01",
                    home_address="1 Market St",
                    selected_departure_utc="2026-05-01 16:00Z",
                    status="active",
                    trip_status="active",
                    created_at=datetime(2026, 4, 1, 12, 0, 0),
                ))
                await s.commit()

        asyncio.run(_seed())

        with patch("scripts.backfill_flight_snapshots.lookup_flights"), \
                caplog.at_level(logging.WARNING, logger="backfill_flight_snapshots"):
            _run_script(factory, dry_run=False)

        row = _read_trip(factory, trip_id)
        # Fallback to UTC hour = 16
        assert row.flight_info["departure_local_hour"] == 16
        assert any("AIRPORT_TIMEZONES missing" in r.message for r in caplog.records)

    def test_skips_when_api_returns_empty(self, test_session):
        factory, _ = test_session
        trip_id = uuid.uuid4()
        _seed_trip_needs_api(
            factory, uuid.uuid4(), trip_id, datetime(2026, 4, 1, 12, 0, 0)
        )

        with patch(
            "scripts.backfill_flight_snapshots.lookup_flights", return_value=[]
        ):
            counts = _run_script(factory, dry_run=False)

        assert counts["skipped"] == 1
        assert counts["written"] == 0
        row = _read_trip(factory, trip_id)
        assert row.flight_info is None
