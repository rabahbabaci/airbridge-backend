"""Tests for GET /v1/users/me and PUT /v1/users/preferences."""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.middleware.auth import get_required_user
from app.main import app


def _make_mock_user(**overrides):
    user = MagicMock()
    user.id = overrides.get("id", uuid.uuid4())
    user.email = overrides.get("email", "test@example.com")
    user.phone_number = overrides.get("phone_number", "+1234567890")
    user.display_name = overrides.get("display_name", "Test User")
    user.auth_provider = overrides.get("auth_provider", "google")
    user.trip_count = overrides.get("trip_count", 1)
    user.subscription_status = overrides.get("subscription_status", "none")
    user.preferred_transport_mode = overrides.get("preferred_transport_mode", None)
    user.preferred_security_access = overrides.get("preferred_security_access", None)
    user.preferred_bag_count = overrides.get("preferred_bag_count", None)
    user.preferred_children = overrides.get("preferred_children", None)
    user.preferred_nav_app = overrides.get("preferred_nav_app", None)
    user.preferred_rideshare_app = overrides.get("preferred_rideshare_app", None)
    return user


@pytest.fixture
def authed_client():
    """Client with get_required_user overridden to return a mock user."""
    mock_user = _make_mock_user()

    async def _override():
        return mock_user

    app.dependency_overrides[get_required_user] = _override
    yield TestClient(app), mock_user
    app.dependency_overrides.pop(get_required_user, None)


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
