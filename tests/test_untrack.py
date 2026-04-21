"""Tests for POST /v1/trips/{id}/untrack endpoint."""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.models import Trip, User


class TestUntrackRealDB:
    """Real-DB tests for POST /v1/trips/{id}/untrack."""

    def _seed(self, factory, user_id, trip_id, status="active", trip_count=1, **kwargs):
        async def _do():
            async with factory() as s:
                existing = await s.get(User, user_id)
                if not existing:
                    s.add(User(
                        id=user_id,
                        trip_count=trip_count,
                        subscription_status=kwargs.get("subscription_status", "none"),
                    ))
                s.add(Trip(
                    id=trip_id,
                    user_id=user_id,
                    input_mode="flight_number",
                    flight_number="AA100",
                    departure_date="2026-04-10",
                    home_address="123 Main St",
                    selected_departure_utc="2026-04-10 14:00Z",
                    status=status,
                    trip_status=status,
                    projected_timeline=kwargs.get("projected_timeline", {
                        "leave_home_at": "2026-04-10T08:00:00+00:00",
                        "arrive_airport_at": "2026-04-10T09:00:00+00:00",
                        "clear_security_at": "2026-04-10T09:30:00+00:00",
                        "at_gate_at": "2026-04-10T09:45:00+00:00",
                        "departure_utc": "2026-04-10T14:00:00+00:00",
                        "computed_at": "2026-04-10T06:00:00+00:00",
                    }),
                    push_count=kwargs.get("push_count", 2),
                    sms_count=kwargs.get("sms_count", 1),
                    actual_depart_at=kwargs.get("actual_depart_at",
                        datetime(2026, 4, 10, 8, 15, tzinfo=timezone.utc)),
                    morning_email_sent_at=kwargs.get("morning_email_sent_at",
                        datetime(2026, 4, 10, 6, 0, tzinfo=timezone.utc)),
                    time_to_go_push_sent_at=kwargs.get("time_to_go_push_sent_at",
                        datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)),
                    auto_completed=kwargs.get("auto_completed", False),
                    feedback_requested_at=kwargs.get("feedback_requested_at",
                        datetime(2026, 4, 10, 15, 0, tzinfo=timezone.utc)),
                ))
                await s.commit()
        asyncio.run(_do())

    def _read_trip(self, factory, trip_id):
        async def _do():
            async with factory() as s:
                return await s.get(Trip, trip_id)
        return asyncio.run(_do())

    def _read_user(self, factory, user_id):
        async def _do():
            async with factory() as s:
                return await s.get(User, user_id)
        return asyncio.run(_do())

    def test_untrack_active_clears_all_fields(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        mock_user.trip_count = 2
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="active", trip_count=2)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "untracked"
        assert data["trip_count"] == 1

        row = self._read_trip(factory, trip_id)
        assert row.status == "draft"
        assert row.trip_status == "draft"
        assert row.projected_timeline is None
        assert row.last_pushed_leave_home_at is None
        assert row.push_count == 0
        assert row.time_to_go_push_sent_at is None
        assert row.sms_count == 0
        assert row.actual_depart_at is None
        assert row.auto_completed is False
        assert row.feedback_requested_at is None
        # selected_departure_utc should NOT be cleared
        assert row.selected_departure_utc == "2026-04-10 14:00Z"

    def test_untrack_en_route(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        mock_user.trip_count = 3
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="en_route", trip_count=3)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.trip_status == "draft"
        assert row.projected_timeline is None

    def test_untrack_at_airport(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="at_airport", trip_count=1)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 200
        assert resp.json()["trip_count"] == 0

    def test_untrack_at_gate(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="at_gate", trip_count=1)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.trip_status == "draft"

    def test_untrack_draft_returns_409(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft", trip_count=0,
                   projected_timeline=None, push_count=0, sms_count=0,
                   actual_depart_at=None, morning_email_sent_at=None,
                   time_to_go_push_sent_at=None, feedback_requested_at=None)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 409
        assert "draft" in resp.json()["detail"]

    def test_untrack_complete_returns_409(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="complete", trip_count=5)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 409
        assert "complete" in resp.json()["detail"]

    def test_trip_count_floor_zero(self, authed_db_client):
        """When trip_count is already 0, untrack should keep it at 0."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="active", trip_count=0)

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 200
        assert resp.json()["trip_count"] == 0

        user = self._read_user(factory, mock_user.id)
        assert user.trip_count == 0

    def test_non_owner_returns_404(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        other_user_id = uuid.uuid4()
        trip_id = uuid.uuid4()

        async def _seed():
            async with factory() as s:
                s.add(User(id=mock_user.id, trip_count=0, subscription_status="none"))
                s.add(User(id=other_user_id, trip_count=1, subscription_status="none"))
                s.add(Trip(id=trip_id, user_id=other_user_id, input_mode="flight_number",
                           flight_number="AA100", departure_date="2026-04-10",
                           home_address="Their Home", status="active", trip_status="active"))
                await s.commit()
        asyncio.run(_seed())

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 404

    def test_pro_user_untrack_decrements_normally(self, authed_db_client):
        """Pro user (subscribed) untracking still decrements trip_count — unconditional."""
        client, factory, mock_user = authed_db_client
        mock_user.trip_count = 5
        mock_user.subscription_status = "active"
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="active", trip_count=5,
                   subscription_status="active")

        resp = client.post(f"/v1/trips/{trip_id}/untrack")
        assert resp.status_code == 200
        assert resp.json()["trip_count"] == 4

    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.post(f"/v1/trips/{uuid.uuid4()}/untrack")
        assert resp.status_code == 401

    def test_no_db_returns_untracked(self):
        """Thin smoke: db=None path."""
        from app.db import get_db
        from app.main import app
        from app.api.middleware.auth import get_required_user
        from tests.conftest import FakeUser

        mock_user = FakeUser()

        async def _override_db():
            yield None

        async def _override_auth():
            return mock_user

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_required_user] = _override_auth
        try:
            c = TestClient(app)
            resp = c.post(f"/v1/trips/{uuid.uuid4()}/untrack")
            assert resp.status_code == 200
            assert resp.json()["status"] == "untracked"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_required_user, None)
