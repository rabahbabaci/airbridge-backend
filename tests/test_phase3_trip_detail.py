"""Phase 3 — GET /v1/trips/{id} fat-detail response + latest_recommendation
writes at track time and during the polling tick."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import Trip, User


MOCK_FLIGHTS = [
    {
        "flight_number": "AA100",
        "airline_name": "American Airlines",
        "origin_iata": "JFK",
        "destination_iata": "LAX",
        "origin_name": "John F. Kennedy International Airport",
        "destination_name": "Los Angeles International Airport",
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
    }
]


def _clear_caches():
    from app.services import flight_snapshot_service as fss
    fss._flight_cache.clear()


class TestTrackPopulatesLatestRecommendation:
    def _seed(self, factory, user_id, trip_id):
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
                    status="draft",
                    trip_status="draft",
                ))
                await s.commit()
        asyncio.run(_do())

    def _read(self, factory, trip_id):
        async def _do():
            async with factory() as s:
                return await s.get(Trip, trip_id)
        return asyncio.run(_do())

    @patch("app.services.flight_snapshot_service.lookup_flights", return_value=MOCK_FLIGHTS)
    def test_track_populates_latest_recommendation(self, _mock_lookup, authed_db_client):
        """Track invokes compute_recommendation and marshals the response onto the row."""
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock as _AsyncMock

        _clear_caches()
        client, factory, user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, user.id, trip_id)

        fake_response = MagicMock()
        fake_response.leave_home_at = datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc)
        fake_response.segments = [
            MagicMock(id="transport", label="Drive to JFK", duration_minutes=45, advice=""),
            MagicMock(id="tsa", label="TSA", duration_minutes=18, advice="wait:18"),
            MagicMock(id="walk_to_gate", label="Gate", duration_minutes=7, advice="Gate B42"),
        ]
        fake_response.home_coordinates = {"lat": 40.7, "lng": -74.0}
        fake_response.terminal_coordinates = {"lat": 40.64, "lng": -73.78}
        fake_response.origin_airport_code = "JFK"

        with patch(
            "app.services.recommendation_service.compute_recommendation",
            new=_AsyncMock(return_value=fake_response),
        ):
            resp = client.post(f"/v1/trips/{trip_id}/track")
        assert resp.status_code == 200

        row = self._read(factory, trip_id)
        assert row.latest_recommendation is not None
        lr = row.latest_recommendation
        assert isinstance(lr["segments"], list) and len(lr["segments"]) == 3
        assert lr["segments"][0]["id"] == "transport"
        assert lr["segments"][0]["duration_minutes"] == 45
        assert lr["home_coordinates"] == {"lat": 40.7, "lng": -74.0}
        assert lr["terminal_coordinates"] == {"lat": 40.64, "lng": -73.78}
        assert lr["computed_at"] is not None
        for seg in lr["segments"]:
            assert {"id", "label", "duration_minutes", "advice"} <= seg.keys()


class TestPollingLatestRecommendationWrites:
    def _make_trip(self, trip_status="active"):
        dep = datetime.now(tz=timezone.utc) + timedelta(hours=5)
        trip = MagicMock()
        trip.id = "trip-xyz"
        trip.user_id = "user-abc"
        trip.input_mode = "flight_number"
        trip.flight_number = "UA100"
        trip.departure_date = dep.date().isoformat()
        trip.selected_departure_utc = dep.isoformat()
        trip.status = trip_status
        trip.trip_status = trip_status
        trip.flight_info = {
            "airline": "United Airlines",
            "flight_number": "UA100",
            "origin_iata": "SFO",
            "destination_iata": "ORD",
            "destination_name": "O'Hare International Airport",
            "scheduled_departure_at": dep.isoformat(),
            "scheduled_departure_local": "2099-01-01 09:00",
            "terminal": "3",
            "departure_local_hour": 9,
            "snapshot_taken_at": "2026-04-01T12:00:00+00:00",
        }
        trip.flight_status = {
            "gate": "C10",
            "status": "Scheduled",
            "delay_minutes": 0,
            "cancelled": False,
            "last_updated_at": (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat(),
        }
        trip.latest_recommendation = None
        trip.projected_timeline = None
        trip.preferences_json = None
        trip.push_count = 0
        trip.last_pushed_leave_home_at = None
        trip.morning_email_sent_at = None
        trip.time_to_go_push_sent_at = None
        trip.sms_count = 0
        trip.auto_completed = False
        trip.feedback_requested_at = None

        user = MagicMock()
        user.id = "user-abc"
        user.email = "t@e.com"
        user.phone_number = "+1"
        user.trip_count = 1
        user.subscription_status = "active"
        trip.user = user
        return trip

    @pytest.mark.asyncio
    async def test_active_phase_tick_writes_latest_recommendation(self):
        from app.services.polling_agent import _process_trip

        trip = self._make_trip(trip_status="active")
        session = AsyncMock()

        fake_response = MagicMock()
        fake_response.leave_home_at = datetime.now(tz=timezone.utc) + timedelta(hours=3)
        fake_response.segments = [
            MagicMock(id="transport", label="Drive to SFO", duration_minutes=30, advice="x"),
            MagicMock(id="tsa", label="TSA", duration_minutes=15, advice="wait:15"),
        ]
        fake_response.home_coordinates = {"lat": 37.7, "lng": -122.4}
        fake_response.terminal_coordinates = {"lat": 37.6, "lng": -122.3}
        fake_response.origin_airport_code = "SFO"

        patches = [
            patch(
                "app.services.polling_agent.lookup_flights",
                return_value=[
                    dict(MOCK_FLIGHTS[0], origin_iata="SFO", destination_iata="ORD"),
                ],
            ),
            patch(
                "app.services.polling_agent._check_interaction_signals",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.polling_agent.recompute_recommendation",
                new=AsyncMock(return_value=fake_response),
            ),
            patch(
                "app.services.polling_agent.send_trip_notification",
                new=AsyncMock(return_value=True),
            ),
        ]
        for p in patches:
            p.start()
        try:
            await _process_trip(trip, session)
        finally:
            for p in patches:
                p.stop()

        assert trip.latest_recommendation is not None
        lr = trip.latest_recommendation
        assert len(lr["segments"]) == 2
        assert lr["segments"][0]["id"] == "transport"
        assert lr["home_coordinates"] == {"lat": 37.7, "lng": -122.4}
        assert lr["terminal_coordinates"] == {"lat": 37.6, "lng": -122.3}
        assert lr["computed_at"] is not None

    @pytest.mark.asyncio
    async def test_at_gate_tick_does_not_touch_latest_recommendation(self):
        from app.services.polling_agent import _process_trip

        trip = self._make_trip(trip_status="at_gate")
        # Pre-existing value — Phase 2 contract says en_route/at_airport/at_gate
        # do NOT recompute, so latest_recommendation must stay untouched.
        trip.latest_recommendation = {"segments": [{"id": "frozen"}]}
        session = AsyncMock()

        patches = [
            patch(
                "app.services.polling_agent.lookup_flights",
                return_value=[
                    dict(MOCK_FLIGHTS[0], origin_iata="SFO", destination_iata="ORD"),
                ],
            ),
            patch(
                "app.services.polling_agent._check_interaction_signals",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.polling_agent.recompute_recommendation",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch(
                "app.services.polling_agent.send_trip_notification",
                new=AsyncMock(return_value=True),
            ),
        ]
        for p in patches:
            p.start()
        try:
            await _process_trip(trip, session)
        finally:
            for p in patches:
                p.stop()

        assert trip.latest_recommendation == {"segments": [{"id": "frozen"}]}


class TestGetTripDetail:
    def _seed_tracked(self, factory, user_id, trip_id):
        async def _do():
            async with factory() as s:
                s.add(User(id=user_id, trip_count=1, subscription_status="none"))
                s.add(Trip(
                    id=trip_id,
                    user_id=user_id,
                    input_mode="flight_number",
                    flight_number="AA100",
                    origin_iata="JFK",
                    destination_iata="LAX",
                    airline="American Airlines",
                    departure_date="2026-04-10",
                    home_address="123 Main St",
                    selected_departure_utc="2026-04-10 14:00Z",
                    status="active",
                    trip_status="active",
                    projected_timeline={"leave_home_at": "2026-04-10T10:00:00+00:00"},
                    flight_info={
                        "airline": "American Airlines",
                        "flight_number": "AA100",
                        "origin_iata": "JFK",
                        "destination_iata": "LAX",
                        "destination_name": "Los Angeles International Airport",
                        "scheduled_departure_at": "2026-04-10T14:00:00+00:00",
                        "scheduled_departure_local": "2026-04-10 10:00",
                        "terminal": "8",
                        "departure_local_hour": 10,
                    },
                    flight_status={
                        "gate": "B42",
                        "status": "Scheduled",
                        "delay_minutes": 0,
                        "cancelled": False,
                    },
                    latest_recommendation={
                        "segments": [
                            {"id": "transport", "label": "Drive", "duration_minutes": 30, "advice": ""}
                        ],
                        "home_coordinates": {"lat": 40.7, "lng": -74.0},
                        "terminal_coordinates": {"lat": 40.6, "lng": -73.7},
                        "computed_at": "2026-04-21T14:00:00+00:00",
                    },
                    preferences_json='{"transport_mode":"driving"}',
                ))
                await s.commit()
        asyncio.run(_do())

    def test_get_trip_detail_returns_full_shape(self, authed_db_client):
        client, factory, user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed_tracked(factory, user.id, trip_id)

        resp = client.get(f"/v1/trips/{trip_id}")
        assert resp.status_code == 200
        body = resp.json()

        expected_keys = {
            "trip_id", "flight_number", "departure_date", "home_address",
            "status", "selected_departure_utc", "preferences_json",
            "origin_iata", "destination_iata", "airline",
            "projected_timeline", "flight_info", "flight_status",
            "latest_recommendation",
        }
        assert expected_keys <= set(body.keys())

        assert body["origin_iata"] == "JFK"
        assert body["destination_iata"] == "LAX"
        assert body["airline"] == "American Airlines"
        assert body["flight_info"]["destination_name"] == "Los Angeles International Airport"
        assert body["flight_info"]["scheduled_departure_local"] == "2026-04-10 10:00"
        assert body["flight_status"]["gate"] == "B42"
        assert body["latest_recommendation"]["segments"][0]["id"] == "transport"
        assert body["latest_recommendation"]["home_coordinates"] == {"lat": 40.7, "lng": -74.0}
        assert body["projected_timeline"]["leave_home_at"] == "2026-04-10T10:00:00+00:00"

    def test_get_trip_detail_404_for_unknown_id(self, authed_db_client):
        client, _, _ = authed_db_client
        resp = client.get(f"/v1/trips/{uuid.uuid4()}")
        assert resp.status_code == 404
