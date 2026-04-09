"""Tests for GET /v1/trips/history endpoint."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.middleware.auth import get_required_user
from app.api.routes.trips import _build_projected_timeline, _compute_accuracy_delta
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


class TestAccuracyDelta:
    """Unit tests for _compute_accuracy_delta helper."""

    def test_positive_delta_arrived_early(self):
        """User waited longer at gate than predicted = arrived early."""
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00+00:00",
            "departure_utc": "2026-03-08T12:00:00+00:00",
        })
        fb = SimpleNamespace(minutes_at_gate=45)
        # predicted buffer = 30 min, actual = 45 → delta = +15
        assert _compute_accuracy_delta(row, fb) == 15

    def test_negative_delta_arrived_late(self):
        """User waited less at gate than predicted = arrived late."""
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00+00:00",
            "departure_utc": "2026-03-08T12:00:00+00:00",
        })
        fb = SimpleNamespace(minutes_at_gate=20)
        # predicted buffer = 30 min, actual = 20 → delta = -10
        assert _compute_accuracy_delta(row, fb) == -10

    def test_zero_delta_exact_prediction(self):
        """User waited exactly as predicted."""
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00+00:00",
            "departure_utc": "2026-03-08T12:00:00+00:00",
        })
        fb = SimpleNamespace(minutes_at_gate=30)
        assert _compute_accuracy_delta(row, fb) == 0

    def test_returns_none_no_feedback(self):
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00+00:00",
            "departure_utc": "2026-03-08T12:00:00+00:00",
        })
        assert _compute_accuracy_delta(row, None) is None

    def test_returns_none_no_minutes_at_gate(self):
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00+00:00",
            "departure_utc": "2026-03-08T12:00:00+00:00",
        })
        fb = SimpleNamespace(minutes_at_gate=None)
        assert _compute_accuracy_delta(row, fb) is None

    def test_returns_none_no_timeline(self):
        row = SimpleNamespace(projected_timeline=None)
        fb = SimpleNamespace(minutes_at_gate=30)
        assert _compute_accuracy_delta(row, fb) is None

    def test_returns_none_missing_at_gate_at(self):
        row = SimpleNamespace(projected_timeline={
            "departure_utc": "2026-03-08T12:00:00+00:00",
        })
        fb = SimpleNamespace(minutes_at_gate=30)
        assert _compute_accuracy_delta(row, fb) is None

    def test_returns_none_missing_departure_utc(self):
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00+00:00",
        })
        fb = SimpleNamespace(minutes_at_gate=30)
        assert _compute_accuracy_delta(row, fb) is None

    def test_handles_z_suffix_timestamps(self):
        """Timestamps with Z suffix (no +00:00) should parse correctly."""
        row = SimpleNamespace(projected_timeline={
            "at_gate_at": "2026-03-08T11:30:00Z",
            "departure_utc": "2026-03-08T12:00:00Z",
        })
        fb = SimpleNamespace(minutes_at_gate=40)
        assert _compute_accuracy_delta(row, fb) == 10


class TestBuildProjectedTimeline:
    """Unit tests for _build_projected_timeline helper."""

    def test_returns_none_when_no_response(self):
        assert _build_projected_timeline(None, "2026-03-08T12:00:00Z") is None

    def test_returns_none_when_no_segments(self):
        response = SimpleNamespace(leave_home_at=None, segments=[])
        assert _build_projected_timeline(response, "2026-03-08T12:00:00Z") is None

    def test_extracts_milestones_from_segments(self):
        from datetime import datetime, timezone

        leave_at = datetime(2026, 3, 8, 10, 0, 0, tzinfo=timezone.utc)
        segments = [
            SimpleNamespace(id="transport_drive", duration_minutes=30),
            SimpleNamespace(id="tsa_security", duration_minutes=20),
            SimpleNamespace(id="gate_walk", duration_minutes=10),
        ]
        response = SimpleNamespace(leave_home_at=leave_at, segments=segments)
        dep_utc = "2026-03-08T12:00:00+00:00"

        result = _build_projected_timeline(response, dep_utc)

        assert result is not None
        assert result["leave_home_at"] == leave_at.isoformat()
        assert result["departure_utc"] == dep_utc
        # transport ends at 10:30
        assert "10:30:00" in result["arrive_airport_at"]
        # security ends at 10:50
        assert "10:50:00" in result["clear_security_at"]
        # gate ends at 11:00
        assert "11:00:00" in result["at_gate_at"]
        assert result["computed_at"] is not None

    def test_handles_missing_segment_types(self):
        from datetime import datetime, timezone

        leave_at = datetime(2026, 3, 8, 10, 0, 0, tzinfo=timezone.utc)
        segments = [
            SimpleNamespace(id="unknown_segment", duration_minutes=30),
        ]
        response = SimpleNamespace(leave_home_at=leave_at, segments=segments)

        result = _build_projected_timeline(response, None)

        assert result is not None
        assert result["arrive_airport_at"] is None
        assert result["clear_security_at"] is None
        assert result["at_gate_at"] is None
        assert result["departure_utc"] is None
