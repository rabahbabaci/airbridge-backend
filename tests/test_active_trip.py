"""Tests for GET /v1/trips/active, GET /v1/trips/active-list, GET /v1/trips/{trip_id}, and POST /v1/trips/{trip_id}/track."""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.middleware.auth import get_required_user
from app.db import get_db
from app.db.models import Trip, User
from app.main import app


class FakeUser:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.email = kwargs.get("email", "test@example.com")
        self.phone_number = kwargs.get("phone_number", "+1234567890")
        self.display_name = kwargs.get("display_name", "Test User")
        self.auth_provider = kwargs.get("auth_provider", "google")
        self.trip_count = kwargs.get("trip_count", 1)
        self.subscription_status = kwargs.get("subscription_status", "none")
        self.preferred_transport_mode = kwargs.get("preferred_transport_mode", None)
        self.preferred_security_access = kwargs.get("preferred_security_access", None)
        self.preferred_bag_count = kwargs.get("preferred_bag_count", None)
        self.preferred_children = kwargs.get("preferred_children", None)
        self.preferred_nav_app = kwargs.get("preferred_nav_app", None)
        self.preferred_rideshare_app = kwargs.get("preferred_rideshare_app", None)


async def _override_get_db():
    yield None


@pytest.fixture
def authed_client():
    mock_user = FakeUser()

    async def _override():
        return mock_user

    app.dependency_overrides[get_required_user] = _override
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_user
    app.dependency_overrides.pop(get_required_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_active_trip_no_auth_returns_401(client: TestClient):
    resp = client.get("/v1/trips/active")
    assert resp.status_code == 401


def test_active_trip_returns_null_no_db(authed_client):
    client, _ = authed_client
    resp = client.get("/v1/trips/active")
    assert resp.status_code == 200
    assert resp.json() == {"trip": None}


def test_get_trip_no_auth_returns_401(client: TestClient):
    resp = client.get(f"/v1/trips/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_get_trip_not_found(authed_client):
    client, _ = authed_client
    resp = client.get(f"/v1/trips/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_track_trip_no_auth_returns_401(client: TestClient):
    resp = client.post(f"/v1/trips/{uuid.uuid4()}/track")
    assert resp.status_code == 401


def test_track_trip_no_db_returns_tracked(authed_client):
    client, _ = authed_client
    trip_id = str(uuid.uuid4())
    resp = client.post(f"/v1/trips/{trip_id}/track")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "tracked"
    assert body["trip_id"] == trip_id
    assert body["trip_count"] == 0


def test_update_trip_no_auth_returns_401(client: TestClient):
    resp = client.put(f"/v1/trips/{uuid.uuid4()}", json={"home_address": "456 Oak Ave"})
    assert resp.status_code == 401


def test_update_trip_no_db_returns_200(authed_client):
    client, _ = authed_client
    trip_id = str(uuid.uuid4())
    resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "456 Oak Ave"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "updated"
    assert body["trip_id"] == trip_id


# ---------------------------------------------------------------------------
# GET /v1/trips/active-list
# ---------------------------------------------------------------------------

def test_active_list_no_auth_returns_401(client: TestClient):
    resp = client.get("/v1/trips/active-list")
    assert resp.status_code == 401


def test_active_list_no_db_returns_empty(authed_client):
    """Thin smoke: db=None returns empty list."""
    client, _ = authed_client
    resp = client.get("/v1/trips/active-list")
    assert resp.status_code == 200
    assert resp.json() == {"trips": []}


class TestActiveListRealDB:
    """Real-DB tests for GET /v1/trips/active-list."""

    def _seed_trips(self, factory, user_id, trips_data):
        """Seed multiple trips into the real test DB."""
        async def _do():
            async with factory() as s:
                s.add(User(id=user_id, trip_count=len(trips_data), subscription_status="none"))
                for td in trips_data:
                    s.add(Trip(
                        id=td["id"],
                        user_id=user_id,
                        input_mode=td.get("input_mode", "flight_number"),
                        flight_number=td.get("flight_number"),
                        departure_date=td["departure_date"],
                        home_address=td.get("home_address", "123 Main St"),
                        status=td["status"],
                        trip_status=td["status"],
                        origin_iata=td.get("origin_iata"),
                        destination_iata=td.get("destination_iata"),
                        airline=td.get("airline"),
                        projected_timeline=td.get("projected_timeline"),
                    ))
                await s.commit()
        asyncio.run(_do())

    def test_no_trips_returns_empty(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        # Seed user with no trips
        async def _seed():
            async with factory() as s:
                s.add(User(id=mock_user.id, trip_count=0, subscription_status="none"))
                await s.commit()
        asyncio.run(_seed())

        resp = client.get("/v1/trips/active-list")
        assert resp.status_code == 200
        assert resp.json()["trips"] == []

    def test_only_drafts(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        t1 = uuid.uuid4()
        t2 = uuid.uuid4()
        self._seed_trips(factory, mock_user.id, [
            {"id": t1, "departure_date": "2026-04-12", "status": "draft", "flight_number": "AA100"},
            {"id": t2, "departure_date": "2026-04-15", "status": "draft", "flight_number": "UA200"},
        ])

        resp = client.get("/v1/trips/active-list")
        assert resp.status_code == 200
        trips = resp.json()["trips"]
        assert len(trips) == 2
        # Ordered by departure_date ASC
        assert trips[0]["departure_date"] == "2026-04-12"
        assert trips[1]["departure_date"] == "2026-04-15"

    def test_mixed_statuses_excludes_complete(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trips_data = [
            {"id": uuid.uuid4(), "departure_date": "2026-04-10", "status": "draft", "flight_number": "AA1"},
            {"id": uuid.uuid4(), "departure_date": "2026-04-11", "status": "active", "flight_number": "AA2",
             "origin_iata": "JFK", "destination_iata": "LAX", "airline": "American Airlines",
             "projected_timeline": {"leave_home_at": "2026-04-11T08:00:00+00:00"}},
            {"id": uuid.uuid4(), "departure_date": "2026-04-12", "status": "en_route", "flight_number": "AA3"},
            {"id": uuid.uuid4(), "departure_date": "2026-04-13", "status": "at_airport", "flight_number": "AA4"},
            {"id": uuid.uuid4(), "departure_date": "2026-04-14", "status": "at_gate", "flight_number": "AA5"},
            {"id": uuid.uuid4(), "departure_date": "2026-04-09", "status": "complete", "flight_number": "AA6"},
        ]
        self._seed_trips(factory, mock_user.id, trips_data)

        resp = client.get("/v1/trips/active-list")
        assert resp.status_code == 200
        trips = resp.json()["trips"]
        # 5 non-complete trips, complete excluded
        assert len(trips) == 5
        statuses = [t["status"] for t in trips]
        assert "complete" not in statuses

    def test_ordering_by_departure_date_asc(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        self._seed_trips(factory, mock_user.id, [
            {"id": uuid.uuid4(), "departure_date": "2026-04-20", "status": "draft", "flight_number": "X1"},
            {"id": uuid.uuid4(), "departure_date": "2026-04-10", "status": "active", "flight_number": "X2"},
            {"id": uuid.uuid4(), "departure_date": "2026-04-15", "status": "en_route", "flight_number": "X3"},
        ])

        resp = client.get("/v1/trips/active-list")
        trips = resp.json()["trips"]
        dates = [t["departure_date"] for t in trips]
        assert dates == ["2026-04-10", "2026-04-15", "2026-04-20"]

    def test_other_users_trips_excluded(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        other_user_id = uuid.uuid4()

        async def _seed():
            async with factory() as s:
                s.add(User(id=mock_user.id, trip_count=0, subscription_status="none"))
                s.add(User(id=other_user_id, trip_count=1, subscription_status="none"))
                # My trip
                s.add(Trip(id=uuid.uuid4(), user_id=mock_user.id, input_mode="flight_number",
                           flight_number="MINE", departure_date="2026-04-10",
                           home_address="My Home", status="active", trip_status="active"))
                # Other user's trip
                s.add(Trip(id=uuid.uuid4(), user_id=other_user_id, input_mode="flight_number",
                           flight_number="THEIRS", departure_date="2026-04-10",
                           home_address="Their Home", status="active", trip_status="active"))
                await s.commit()
        asyncio.run(_seed())

        resp = client.get("/v1/trips/active-list")
        trips = resp.json()["trips"]
        assert len(trips) == 1
        assert trips[0]["flight_number"] == "MINE"

    def test_response_shape(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        self._seed_trips(factory, mock_user.id, [
            {"id": uuid.uuid4(), "departure_date": "2026-04-12", "status": "active",
             "flight_number": "AA100", "origin_iata": "JFK", "destination_iata": "LAX",
             "airline": "American Airlines",
             "projected_timeline": {"leave_home_at": "2026-04-12T08:00:00+00:00"}},
        ])

        resp = client.get("/v1/trips/active-list")
        trip = resp.json()["trips"][0]
        expected_keys = {
            "trip_id", "flight_number", "airline", "origin_iata", "destination_iata",
            "departure_date", "status", "projected_timeline", "home_address",
        }
        assert set(trip.keys()) == expected_keys
        assert trip["flight_number"] == "AA100"
        assert trip["origin_iata"] == "JFK"
        assert trip["destination_iata"] == "LAX"
        assert trip["airline"] == "American Airlines"
        assert trip["projected_timeline"] is not None
        assert trip["status"] == "active"

    def test_includes_created_status(self, authed_db_client):
        """Verify 'created' status (between draft and active) is included."""
        client, factory, mock_user = authed_db_client
        self._seed_trips(factory, mock_user.id, [
            {"id": uuid.uuid4(), "departure_date": "2026-04-12", "status": "created",
             "flight_number": "AA100"},
        ])

        resp = client.get("/v1/trips/active-list")
        trips = resp.json()["trips"]
        assert len(trips) == 1
        assert trips[0]["status"] == "created"
