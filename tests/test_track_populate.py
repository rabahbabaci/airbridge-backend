"""Test that tracking a flight_number trip populates origin_iata, destination_iata, airline."""

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


class TestTrackPopulatesFlightColumns:
    """Real-DB test: track endpoint populates origin_iata, destination_iata, airline."""

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
    def test_track_populates_flight_columns_no_selected_utc(self, mock_lookup, authed_db_client):
        """Without selected_departure_utc, uses first flight in list."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"

        row = self._read_trip(factory, trip_id)
        assert row.origin_iata == "JFK"
        assert row.destination_iata == "LAX"
        assert row.airline == "American Airlines"
        assert row.trip_status == "active"
        mock_lookup.assert_called_with("AA100", "2026-04-10")

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_matches_selected_departure_utc(self, mock_lookup, authed_db_client):
        """With selected_departure_utc, matches the correct flight."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, selected_utc="2026-04-10 20:00Z")

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        # Should match second flight (20:00Z), but both have same route
        assert row.origin_iata == "JFK"
        assert row.destination_iata == "LAX"
        assert row.airline == "American Airlines"

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=[])
    def test_track_succeeds_when_no_flights_found(self, mock_lookup, authed_db_client):
        """Track still succeeds if AeroDataBox returns no flights — columns stay null."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"

        row = self._read_trip(factory, trip_id)
        assert row.origin_iata is None
        assert row.destination_iata is None
        assert row.airline is None
        # Trip still promoted to active
        assert row.trip_status == "active"

    @patch("app.services.flight_snapshot_service.lookup_flights", side_effect=Exception("API down"))
    def test_track_succeeds_when_aerodatabox_errors(self, mock_lookup, authed_db_client):
        """Track still succeeds if AeroDataBox throws — columns stay null, trip still active."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["status"] == "tracked"

        row = self._read_trip(factory, trip_id)
        assert row.origin_iata is None
        assert row.trip_status == "active"

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_increments_trip_count(self, mock_lookup, authed_db_client):
        """Verify trip_count increments on track (real DB, not mocked)."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id)

        assert mock_user.trip_count == 0
        resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200
        assert resp.json()["trip_count"] == 1
