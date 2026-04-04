"""Tests for GET /v1/users/me and PUT /v1/users/preferences."""

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


def _make_mock_user(**overrides):
    return FakeUser(**overrides)


async def _override_get_db():
    yield None


@pytest.fixture
def authed_client():
    """Client with get_required_user overridden to return a mock user."""
    mock_user = _make_mock_user()

    async def _override():
        return mock_user

    app.dependency_overrides[get_required_user] = _override
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_user
    app.dependency_overrides.pop(get_required_user, None)
    app.dependency_overrides.pop(get_db, None)


class TestGetMe:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.get("/v1/users/me")
        assert resp.status_code == 401

    def test_with_auth_returns_profile(self, authed_client):
        client, mock_user = authed_client
        resp = client.get("/v1/users/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == str(mock_user.id)
        assert data["email"] == "test@example.com"
        assert data["trip_count"] == 1
        assert data["tier"] in ("pro", "free")
        assert "preferences" in data
        assert "transport_mode" in data["preferences"]


class TestUpdatePreferences:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.put(
            "/v1/users/preferences",
            json={"transport_mode": "driving"},
        )
        assert resp.status_code == 401

    def test_update_returns_preferences(self, authed_client):
        client, _ = authed_client
        resp = client.put(
            "/v1/users/preferences",
            json={"transport_mode": "rideshare", "bag_count": 2},
        )
        assert resp.status_code == 200

    def test_partial_update_only_transport_mode(self, authed_client):
        client, _ = authed_client
        resp = client.put(
            "/v1/users/preferences",
            json={"transport_mode": "driving"},
        )
        assert resp.status_code == 200


class TestDeleteAccount:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.delete("/v1/users/me")
        assert resp.status_code == 401

    def test_returns_204_with_auth(self, authed_client):
        client, _ = authed_client
        resp = client.delete("/v1/users/me")
        assert resp.status_code == 204

    def test_response_has_no_body(self, authed_client):
        client, _ = authed_client
        resp = client.delete("/v1/users/me")
        assert resp.status_code == 204
        assert resp.content == b""
