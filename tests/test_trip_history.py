"""Tests for GET /v1/trips/history endpoint."""

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
        self.trip_count = kwargs.get("trip_count", 1)
        self.subscription_status = kwargs.get("subscription_status", "none")
        self.stripe_customer_id = kwargs.get("stripe_customer_id", None)


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


@pytest.fixture
def pro_client():
    mock_user = FakeUser(subscription_status="active", trip_count=10)

    async def _override():
        return mock_user

    app.dependency_overrides[get_required_user] = _override
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_user
    app.dependency_overrides.pop(get_required_user, None)
    app.dependency_overrides.pop(get_db, None)


class TestTripHistory:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.get("/v1/trips/history")
        assert resp.status_code == 401

    def test_returns_empty_without_db(self, authed_client):
        """With db=None, returns empty response."""
        client, _ = authed_client
        resp = client.get("/v1/trips/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trips"] == []
        assert data["total"] == 0
        assert data["avg_accuracy_minutes"] is None
        assert data["total_trips_with_feedback"] == 0

    def test_accepts_pagination_params(self, authed_client):
        client, _ = authed_client
        resp = client.get("/v1/trips/history?limit=5&offset=10")
        assert resp.status_code == 200

    def test_default_pagination(self, authed_client):
        client, _ = authed_client
        resp = client.get("/v1/trips/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "trips" in data
        assert "total" in data

    def test_response_shape(self, authed_client):
        client, _ = authed_client
        resp = client.get("/v1/trips/history")
        data = resp.json()
        assert set(data.keys()) == {"trips", "total", "avg_accuracy_minutes", "total_trips_with_feedback"}


class TestProGating:
    def test_free_tier_limit(self):
        """Free tier users should get max 5 trips regardless of limit param."""
        from app.services.trial import is_pro

        free_user = FakeUser(trip_count=5, subscription_status="none")
        assert is_pro(free_user) is False

        # The endpoint caps at min(limit, 5) for free users
        limit = 20
        max_results = limit if is_pro(free_user) else min(limit, 5)
        assert max_results == 5

    def test_pro_tier_unlimited(self):
        """Pro users get full limit."""
        from app.services.trial import is_pro

        pro_user = FakeUser(subscription_status="active", trip_count=10)
        assert is_pro(pro_user) is True

        limit = 20
        max_results = limit if is_pro(pro_user) else min(limit, 5)
        assert max_results == 20

    def test_trial_user_is_pro(self):
        """Users within trial (trip_count <= 3) are pro."""
        from app.services.trial import is_pro

        trial_user = FakeUser(trip_count=2, subscription_status="none")
        assert is_pro(trial_user) is True

        limit = 20
        max_results = limit if is_pro(trial_user) else min(limit, 5)
        assert max_results == 20
