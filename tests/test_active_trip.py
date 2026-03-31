"""Tests for GET /v1/trips/active, GET /v1/trips/{trip_id}, and POST /v1/trips/{trip_id}/track."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.middleware.auth import get_required_user
from app.db import get_db
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
