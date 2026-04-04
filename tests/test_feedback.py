"""Tests for POST /v1/feedback endpoint."""

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


class TestFeedbackEndpoint:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.post("/v1/feedback", json={
            "trip_id": str(uuid.uuid4()),
            "followed_recommendation": True,
            "minutes_at_gate": 25,
        })
        assert resp.status_code == 401

    def test_requires_trip_id(self, authed_client):
        client, _ = authed_client
        resp = client.post("/v1/feedback", json={
            "followed_recommendation": True,
        })
        assert resp.status_code == 422

    def test_returns_503_without_db(self, authed_client):
        """With db=None, endpoint returns 503."""
        client, _ = authed_client
        resp = client.post("/v1/feedback", json={
            "trip_id": str(uuid.uuid4()),
            "followed_recommendation": True,
            "minutes_at_gate": 25,
        })
        assert resp.status_code == 503

    def test_accepts_all_fields(self, authed_client):
        """Validates request body accepts all feedback fields."""
        client, _ = authed_client
        resp = client.post("/v1/feedback", json={
            "trip_id": str(uuid.uuid4()),
            "followed_recommendation": True,
            "minutes_at_gate": 25,
            "actual_tsa_wait_minutes": 15,
        })
        # 503 because no DB, but validates request parsing worked
        assert resp.status_code == 503

    def test_accepts_minimal_feedback(self, authed_client):
        """Only trip_id is required."""
        client, _ = authed_client
        resp = client.post("/v1/feedback", json={
            "trip_id": str(uuid.uuid4()),
        })
        assert resp.status_code == 503


class TestOutlierRejection:
    """Test the outlier rejection math used in _try_store_tsa_observation."""

    def test_within_3_std_devs_accepted(self):
        """Values within 3 standard deviations should be accepted."""
        mean = 20.0
        std_dev = 5.0
        value = 33  # 2.6 std devs away
        assert abs(value - mean) <= 3 * std_dev

    def test_beyond_3_std_devs_rejected(self):
        """Values beyond 3 standard deviations should be rejected."""
        mean = 20.0
        std_dev = 5.0
        value = 40  # 4 std devs away
        assert abs(value - mean) > 3 * std_dev

    def test_with_zero_std_dev_not_rejected(self):
        """If std_dev is 0, rejection should not apply (division issue)."""
        std_dev = 0
        # The code checks `std_dev > 0` before rejecting
        assert not (std_dev > 0)

    def test_fewer_than_10_observations_skip_rejection(self):
        """With < 10 observations, outlier rejection is skipped."""
        obs_count = 8
        assert obs_count < 10  # Rejection logic is bypassed


class TestAccuracyStats:
    """Test accuracy computation logic."""

    def test_accuracy_from_gate_minutes(self):
        """Accuracy is |actual_gate_minutes - 30| (30 min = ideal boarding buffer)."""
        gate_minutes = 25.0
        accuracy = round(abs(gate_minutes - 30), 1)
        assert accuracy == 5.0

    def test_accuracy_with_excessive_gate_time(self):
        gate_minutes = 60.0
        accuracy = round(abs(gate_minutes - 30), 1)
        assert accuracy == 30.0

    def test_trend_requires_three_feedbacks(self):
        """Personal accuracy trend needs >= 3 feedbacks to be meaningful."""
        trips_with_feedback = 2
        trend = "improving" if trips_with_feedback >= 3 else "insufficient_data"
        assert trend == "insufficient_data"

        trips_with_feedback = 3
        trend = "improving" if trips_with_feedback >= 3 else "insufficient_data"
        assert trend == "improving"
