"""Tests for expanded PUT /v1/trips/{id} endpoint."""

import asyncio
import json
import uuid

import pytest

from app.db.models import Trip, User


class TestUpdateTripRealDB:
    """Real-DB tests for PUT /v1/trips/{id}."""

    def _seed(self, factory, user_id, trip_id, status="draft", **kwargs):
        async def _do():
            async with factory() as s:
                # Only add user if not already present
                existing = await s.get(User, user_id)
                if not existing:
                    s.add(User(id=user_id, trip_count=0, subscription_status="none"))
                s.add(Trip(
                    id=trip_id,
                    user_id=user_id,
                    input_mode=kwargs.get("input_mode", "flight_number"),
                    flight_number=kwargs.get("flight_number", "AA100"),
                    departure_date=kwargs.get("departure_date", "2026-04-10"),
                    home_address=kwargs.get("home_address", "123 Main St"),
                    selected_departure_utc=kwargs.get("selected_departure_utc"),
                    preferences_json=kwargs.get("preferences_json", '{"transport_mode": "driving"}'),
                    status=status,
                    trip_status=status,
                    projected_timeline=kwargs.get("projected_timeline"),
                ))
                await s.commit()
        asyncio.run(_do())

    def _read_trip(self, factory, trip_id):
        async def _do():
            async with factory() as s:
                return await s.get(Trip, trip_id)
        return asyncio.run(_do())

    def test_edit_draft_updates_fields(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft")

        resp = client.put(f"/v1/trips/{trip_id}", json={
            "flight_number": "ua456",
            "departure_date": "2026-05-01",
            "home_address": "789 New St",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        row = self._read_trip(factory, trip_id)
        assert row.flight_number == "UA456"  # normalized uppercase
        assert row.departure_date == "2026-05-01"
        assert row.home_address == "789 New St"

    def test_edit_draft_projected_timeline_stays_null(self, authed_db_client):
        """Drafts don't have projected_timeline — editing shouldn't create one."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft")

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "New"})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.projected_timeline is None

    def test_edit_active_trip_succeeds(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="active",
                   projected_timeline={"leave_home_at": "old", "computed_at": "old"})

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "456 Oak Ave"})
        assert resp.status_code == 200

    def test_edit_en_route_returns_409(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="en_route")

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "New"})
        assert resp.status_code == 409
        assert "en_route" in resp.json()["detail"]

    def test_edit_at_airport_returns_409(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="at_airport")

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "New"})
        assert resp.status_code == 409

    def test_edit_at_gate_returns_409(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="at_gate")

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "New"})
        assert resp.status_code == 409

    def test_edit_complete_returns_409(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="complete")

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "New"})
        assert resp.status_code == 409
        assert "complete" in resp.json()["detail"]

    def test_partial_update_preserves_other_fields(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   flight_number="AA100", home_address="Original Home",
                   departure_date="2026-04-10")

        # Only update flight_number
        resp = client.put(f"/v1/trips/{trip_id}", json={"flight_number": "DL789"})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        assert row.flight_number == "DL789"
        assert row.home_address == "Original Home"  # preserved
        assert row.departure_date == "2026-04-10"  # preserved

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
                           home_address="Their Home", status="draft", trip_status="draft"))
                await s.commit()
        asyncio.run(_seed())

        resp = client.put(f"/v1/trips/{trip_id}", json={"home_address": "Hacked"})
        assert resp.status_code == 404  # not 403

    def test_nonexistent_trip_returns_404(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        async def _seed():
            async with factory() as s:
                s.add(User(id=mock_user.id, trip_count=0, subscription_status="none"))
                await s.commit()
        asyncio.run(_seed())

        resp = client.put(f"/v1/trips/{uuid.uuid4()}", json={"home_address": "X"})
        assert resp.status_code == 404

    def test_preference_fields_update(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving", "bag_count": 2}')

        resp = client.put(f"/v1/trips/{trip_id}", json={
            "transport_mode": "rideshare",
            "security_access": "precheck",
            "buffer_preference": 45,
        })
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["transport_mode"] == "rideshare"
        assert prefs["security_access"] == "precheck"
        assert prefs["gate_time_minutes"] == 45
        assert prefs["bag_count"] == 2  # preserved from original

    # ── Extended preference fields (Sprint 7 cleanup) ────────────────────
    # bag_count, traveling_with_children, has_boarding_pass,
    # extra_time_minutes, confidence_profile — previously unreachable via PUT.

    def test_edit_draft_updates_bag_count(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving"}')

        resp = client.put(f"/v1/trips/{trip_id}", json={"bag_count": 3})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["bag_count"] == 3

    def test_edit_draft_updates_traveling_with_children(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving"}')

        resp = client.put(f"/v1/trips/{trip_id}", json={"traveling_with_children": True})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["traveling_with_children"] is True

    def test_edit_draft_updates_has_boarding_pass(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving", "has_boarding_pass": true}')

        resp = client.put(f"/v1/trips/{trip_id}", json={"has_boarding_pass": False})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["has_boarding_pass"] is False

    def test_edit_draft_updates_extra_time_minutes(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving"}')

        resp = client.put(f"/v1/trips/{trip_id}", json={"extra_time_minutes": 30})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["extra_time_minutes"] == 30

    def test_edit_draft_updates_confidence_profile(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving"}')

        resp = client.put(f"/v1/trips/{trip_id}", json={"confidence_profile": "safety"})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["confidence_profile"] == "safety"

    def test_edit_all_five_new_prefs_in_one_request(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json='{"transport_mode": "driving"}')

        resp = client.put(f"/v1/trips/{trip_id}", json={
            "bag_count": 2,
            "traveling_with_children": True,
            "has_boarding_pass": False,
            "extra_time_minutes": 15,
            "confidence_profile": "risk",
        })
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["bag_count"] == 2
        assert prefs["traveling_with_children"] is True
        assert prefs["has_boarding_pass"] is False
        assert prefs["extra_time_minutes"] == 15
        assert prefs["confidence_profile"] == "risk"
        # Pre-existing transport_mode should survive the partial update.
        assert prefs["transport_mode"] == "driving"

    def test_partial_update_preserves_existing_new_prefs(self, authed_db_client):
        """Touching transport_mode must not clobber bag_count / has_boarding_pass
        already in preferences_json."""
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        seed_prefs = json.dumps({
            "transport_mode": "driving",
            "bag_count": 3,
            "has_boarding_pass": False,
            "confidence_profile": "safety",
        })
        self._seed(factory, mock_user.id, trip_id, status="draft",
                   preferences_json=seed_prefs)

        resp = client.put(f"/v1/trips/{trip_id}", json={"transport_mode": "rideshare"})
        assert resp.status_code == 200

        row = self._read_trip(factory, trip_id)
        prefs = json.loads(row.preferences_json)
        assert prefs["transport_mode"] == "rideshare"  # changed
        assert prefs["bag_count"] == 3                  # preserved
        assert prefs["has_boarding_pass"] is False      # preserved
        assert prefs["confidence_profile"] == "safety"  # preserved

    def test_bag_count_out_of_range_returns_422(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft")

        resp = client.put(f"/v1/trips/{trip_id}", json={"bag_count": 11})
        assert resp.status_code == 422

    def test_extra_time_invalid_value_returns_422(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft")

        resp = client.put(f"/v1/trips/{trip_id}", json={"extra_time_minutes": 10})
        assert resp.status_code == 422

    def test_confidence_profile_invalid_returns_422(self, authed_db_client):
        client, factory, mock_user = authed_db_client
        trip_id = uuid.uuid4()
        self._seed(factory, mock_user.id, trip_id, status="draft")

        resp = client.put(f"/v1/trips/{trip_id}", json={"confidence_profile": "chaos"})
        assert resp.status_code == 422
