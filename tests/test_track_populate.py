"""Test that tracking a flight_number trip populates flight_info + flight_status
and the denormalized scalars (origin_iata, destination_iata, airline)."""

import asyncio
import uuid
from unittest.mock import patch

import pytest

from app.db.models import Trip, User


MOCK_FLIGHTS = [
    {
        "flight_number": "AA100",
        "airline_name": "American Airlines",
        "origin_iata": "JFK",
        "destination_iata": "LAX",
        "departure_time_utc": "2026-04-10 14:00Z",
        "departure_time_local": "2026-04-10 10:00",
        "arrival_time_utc": "2026-04-10 20:00Z",
        "arrival_time_local": "2026-04-10 17:00",
        "revised_departure_utc": None,
        "revised_departure_local": None,
        "departure_terminal": "8",
        "departure_gate": "B42",
        "arrival_terminal": "5",
        "status": "Scheduled",
        "is_delayed": False,
        "aircraft_model": "Boeing 777-200",
        "origin_name": "John F. Kennedy International Airport",
        "destination_name": "Los Angeles International Airport",
    },
    {
        "flight_number": "AA100",
        "airline_name": "American Airlines",
        "origin_iata": "JFK",
        "destination_iata": "LAX",
        "departure_time_utc": "2026-04-10 20:00Z",
        "departure_time_local": "2026-04-10 16:00",
        "arrival_time_utc": "2026-04-10 23:30Z",
        "arrival_time_local": "2026-04-10 20:30",
        "revised_departure_utc": None,
        "revised_departure_local": None,
        "departure_terminal": "8",
        "departure_gate": None,
        "arrival_terminal": "5",
        "status": "Scheduled",
        "is_delayed": False,
        "aircraft_model": "Boeing 777-200",
        "origin_name": "John F. Kennedy International Airport",
        "destination_name": "Los Angeles International Airport",
    },
]


def _clear_flight_cache():
    """Reset the flight_snapshot_service module cache between tests."""
    from app.services import flight_snapshot_service as fss
    fss._flight_cache.clear()


class TestTrackPopulatesFlightInfo:
    """Real-DB test: track endpoint populates flight_info + flight_status + scalars."""

    def _seed(self, factory, user_id, trip_id, selected_utc=None):
        async def _do():
            async with factory() as s:
                s.add(User(id=user_id, trip_count=0, subscription_status="none"))
                s.add(Trip(
                    id=trip_id,
                    user_id=user_id,
                    input_mode="flight_number",
                    flight_number="AA100",
                    departure_date="2026-04-10",
                    home_address="123 Main St, New York, NY",
                    selected_departure_utc=selected_utc,
                    status="draft",
                    trip_status="draft",
                ))
                await s.commit()
        asyncio.run(_do())

    def _read_trip(self, factory, trip_id):
        async def _do():
            async with factory() as s:
                return await s.get(Trip, trip_id)
        return asyncio.run(_do())

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_populates_flight_info_no_selected_utc(self, mock_lookup, authed_db_client):
        """Without selected_departure_utc, uses first flight in list."""
        _clear_flight_cache()
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "tracked"
        assert body["flight_info"] is not None
        assert body["flight_info"]["origin_iata"] == "JFK"
        assert body["flight_info"]["destination_iata"] == "LAX"
        assert body["flight_info"]["airline"] == "American Airlines"
        assert body["flight_status"] is not None

        row = self._read_trip(factory, trip_id)
        assert row.trip_status == "active"

        # Denormalized scalars
        assert row.origin_iata == "JFK"
        assert row.destination_iata == "LAX"
        assert row.airline == "American Airlines"

        # flight_info shape
        info = row.flight_info
        assert info["airline"] == "American Airlines"
        assert info["flight_number"] == "AA100"
        assert info["origin_iata"] == "JFK"
        assert info["destination_iata"] == "LAX"
        assert info["scheduled_departure_at"] == "2026-04-10T14:00:00+00:00"
        assert info["scheduled_arrival_at"] == "2026-04-10T20:00:00+00:00"
        assert info["aircraft_type"] == "Boeing 777-200"
        assert info["terminal"] == "8"
        assert info["duration_minutes"] == 360  # 14:00 → 20:00 = 6h
        assert info["departure_local_hour"] == 10  # "2026-04-10 10:00" local
        assert info["destination_name"] == "Los Angeles International Airport"
        assert info["scheduled_departure_local"] == "2026-04-10 10:00"
        assert info["snapshot_taken_at"] is not None

        # flight_status shape
        status = row.flight_status
        assert status["gate"] == "B42"
        assert status["status"] == "Scheduled"
        assert status["delay_minutes"] == 0
        assert status["actual_departure_at"] is None
        assert status["cancelled"] is False
        assert status["last_updated_at"] is not None

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_matches_selected_departure_utc(self, mock_lookup, authed_db_client):
        """With selected_departure_utc, matches the correct flight (no gate on second)."""
        _clear_flight_cache()
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, selected_utc="2026-04-10 20:00Z")

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.flight_info["scheduled_departure_at"] == "2026-04-10T20:00:00+00:00"
        assert row.flight_status["gate"] is None  # second mock has no gate

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=[])
    def test_track_succeeds_when_no_flights_found(self, mock_lookup, authed_db_client):
        """Track still succeeds if AeroDataBox returns no flights — flight_info stays null."""
        _clear_flight_cache()
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"

        row = self._read_trip(factory, trip_id)
        assert row.flight_info is None
        assert row.flight_status is None
        assert row.origin_iata is None
        assert row.trip_status == "active"

    @patch("app.services.flight_snapshot_service.lookup_flights", side_effect=Exception("API down"))
    def test_track_succeeds_when_aerodatabox_errors(self, mock_lookup, authed_db_client):
        """Track still succeeds if AeroDataBox throws — flight_info stays null."""
        _clear_flight_cache()
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"

        row = self._read_trip(factory, trip_id)
        assert row.flight_info is None
        assert row.origin_iata is None
        assert row.trip_status == "active"

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_increments_trip_count(self, mock_lookup, authed_db_client):
        """Verify trip_count increments on track (real DB, not mocked)."""
        _clear_flight_cache()
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        assert mock_user.trip_count == 0
        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["trip_count"] == 1

    @patch("app.services.flight_snapshot_service.get_available_flights")
    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_does_not_call_get_available_flights(
        self, mock_lookup, mock_get_available, authed_db_client
    ):
        """Phase 1 contract: the old line-192 get_available_flights call is removed.

        track now reuses the cache populated by compute_recommendation and routes
        through get_selected_flight instead.
        """
        _clear_flight_cache()
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert mock_get_available.call_count == 0

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_cancelled_flight_sets_cancelled_true(self, mock_lookup, authed_db_client):
        """flight_status.cancelled mirrors input status == 'Cancelled'."""
        _clear_flight_cache()
        cancelled = [dict(MOCK_FLIGHTS[0], status="Cancelled")]
        mock_lookup.return_value = cancelled

        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.flight_status["status"] == "Cancelled"
        assert row.flight_status["cancelled"] is True

    @patch("app.services.flight_snapshot_service.lookup_flights")
    def test_track_delayed_flight_computes_delay_minutes(self, mock_lookup, authed_db_client):
        """revised_departure_utc > departure_time_utc → delay_minutes + actual_departure_at."""
        _clear_flight_cache()
        delayed = [dict(
            MOCK_FLIGHTS[0],
            revised_departure_utc="2026-04-10 14:45Z",
            is_delayed=True,
            status="Delayed",
        )]
        mock_lookup.return_value = delayed

        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.flight_status["delay_minutes"] == 45
        assert row.flight_status["actual_departure_at"] == "2026-04-10T14:45:00+00:00"
        assert row.flight_status["cancelled"] is False
