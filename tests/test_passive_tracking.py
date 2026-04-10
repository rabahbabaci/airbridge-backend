"""Tests for smart passive trip tracking: state advancement, phase-aware polling, feedback timing."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.trips import _build_projected_timeline
from app.services.polling_agent import (
    INTERACTION_SIGNALS,
    _advance_trip_state,
    _get_departure_utc,
    _get_timeline_dt,
    _handle_feedback_request,
)
from app.services.trip_state import (
    MONITORABLE_STATUSES,
    STATUS_ORDER,
    advance_status,
    get_trip_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trip(**kwargs):
    trip = MagicMock()
    trip.id = kwargs.get("id", "trip-123")
    trip.user_id = kwargs.get("user_id", "user-456")
    trip.flight_number = kwargs.get("flight_number", "UA100")
    trip.departure_date = kwargs.get("departure_date", "2026-04-05")
    trip.selected_departure_utc = kwargs.get("selected_departure_utc", "2026-04-05T18:00:00+00:00")
    trip.status = kwargs.get("status", "active")
    trip.trip_status = kwargs.get("trip_status", "active")
    trip.projected_timeline = kwargs.get("projected_timeline", None)
    trip.actual_depart_at = kwargs.get("actual_depart_at", None)
    trip.auto_completed = kwargs.get("auto_completed", False)
    trip.feedback_requested_at = kwargs.get("feedback_requested_at", None)
    trip.push_count = kwargs.get("push_count", 0)
    trip.last_pushed_leave_home_at = None
    trip.morning_email_sent_at = None
    trip.time_to_go_push_sent_at = None
    trip.sms_count = 0
    trip.preferences_json = None

    user = MagicMock()
    user.id = trip.user_id
    user.email = "test@example.com"
    user.phone_number = "+1234567890"
    user.trip_count = 1
    user.subscription_status = "none"
    trip.user = user

    return trip


def _make_timeline(
    leave_home_offset_hours=-3,
    arrive_airport_offset_hours=-2,
    clear_security_offset_hours=-1.5,
    at_gate_offset_hours=-1,
    departure_utc="2026-04-05T18:00:00+00:00",
):
    """Build a projected_timeline dict with offsets relative to departure."""
    dep = datetime.fromisoformat(departure_utc)
    return {
        "leave_home_at": (dep + timedelta(hours=leave_home_offset_hours)).isoformat(),
        "arrive_airport_at": (dep + timedelta(hours=arrive_airport_offset_hours)).isoformat(),
        "clear_security_at": (dep + timedelta(hours=clear_security_offset_hours)).isoformat(),
        "at_gate_at": (dep + timedelta(hours=at_gate_offset_hours)).isoformat(),
        "departure_utc": departure_utc,
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Trip state machine
# ---------------------------------------------------------------------------

class TestTripStateMachine:
    def test_monitorable_statuses_expanded(self):
        assert MONITORABLE_STATUSES == {"active", "en_route", "at_airport", "at_gate"}

    def test_status_order(self):
        assert STATUS_ORDER == ["draft", "created", "active", "en_route", "at_airport", "at_gate", "complete"]

    def test_forward_transition_allowed(self):
        trip = _make_trip(trip_status="active")
        advance_status(trip, "en_route")
        assert trip.trip_status == "en_route"

    def test_backward_transition_blocked(self):
        trip = _make_trip(trip_status="en_route")
        with pytest.raises(ValueError, match="only forward"):
            advance_status(trip, "active")

    def test_skip_states_allowed(self):
        trip = _make_trip(trip_status="active")
        advance_status(trip, "complete")
        assert trip.trip_status == "complete"


# ---------------------------------------------------------------------------
# Timeline helpers
# ---------------------------------------------------------------------------

class TestTimelineHelpers:
    def test_get_departure_utc_from_timeline(self):
        trip = _make_trip(projected_timeline=_make_timeline())
        dt = _get_departure_utc(trip)
        assert dt is not None
        assert dt.year == 2026

    def test_get_departure_utc_fallback_to_selected(self):
        trip = _make_trip(projected_timeline=None)
        dt = _get_departure_utc(trip)
        assert dt is not None

    def test_get_timeline_dt(self):
        trip = _make_trip(projected_timeline=_make_timeline())
        dt = _get_timeline_dt(trip, "leave_home_at")
        assert dt is not None

    def test_get_timeline_dt_missing_key(self):
        trip = _make_trip(projected_timeline=_make_timeline())
        dt = _get_timeline_dt(trip, "nonexistent_key")
        assert dt is None

    def test_get_timeline_dt_no_timeline(self):
        trip = _make_trip(projected_timeline=None)
        dt = _get_timeline_dt(trip, "leave_home_at")
        assert dt is None


# ---------------------------------------------------------------------------
# State advancement
# ---------------------------------------------------------------------------

class TestStateAdvancement:
    @pytest.mark.asyncio
    async def test_active_to_en_route_time_based(self):
        """When now >= leave_home_at, trip advances to en_route."""
        timeline = _make_timeline(leave_home_offset_hours=-3)
        trip = _make_trip(trip_status="active", projected_timeline=timeline)
        # Set now to after leave_home_at
        leave_home = datetime.fromisoformat(timeline["leave_home_at"])
        now = leave_home + timedelta(minutes=5)

        session = AsyncMock()
        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "en_route"
        session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_active_stays_if_before_leave_home(self):
        """Trip stays active if now < leave_home_at and no interaction."""
        timeline = _make_timeline(leave_home_offset_hours=-3)
        trip = _make_trip(trip_status="active", projected_timeline=timeline)
        leave_home = datetime.fromisoformat(timeline["leave_home_at"])
        now = leave_home - timedelta(hours=1)

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "active"

    @pytest.mark.asyncio
    async def test_active_to_en_route_via_interaction(self):
        """Interaction signal advances active → en_route with actual_depart_at."""
        timeline = _make_timeline(leave_home_offset_hours=-3)
        trip = _make_trip(trip_status="active", projected_timeline=timeline)
        leave_home = datetime.fromisoformat(timeline["leave_home_at"])
        now = leave_home - timedelta(minutes=5)  # Before leave_home_at

        signal_time = now - timedelta(minutes=2)
        mock_event = MagicMock()
        mock_event.created_at = signal_time

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_event
        session.execute = AsyncMock(return_value=mock_result)

        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "en_route"
        assert trip.actual_depart_at == signal_time

    @pytest.mark.asyncio
    async def test_en_route_to_at_airport(self):
        timeline = _make_timeline()
        trip = _make_trip(trip_status="en_route", projected_timeline=timeline)
        arrive_at = datetime.fromisoformat(timeline["arrive_airport_at"])
        now = arrive_at + timedelta(minutes=5)

        session = AsyncMock()
        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "at_airport"

    @pytest.mark.asyncio
    async def test_at_airport_to_at_gate(self):
        timeline = _make_timeline()
        trip = _make_trip(trip_status="at_airport", projected_timeline=timeline)
        clear_sec = datetime.fromisoformat(timeline["clear_security_at"])
        now = clear_sec + timedelta(minutes=5)

        session = AsyncMock()
        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "at_gate"

    @pytest.mark.asyncio
    async def test_at_gate_to_complete_at_departure_plus_30(self):
        timeline = _make_timeline()
        trip = _make_trip(trip_status="at_gate", projected_timeline=timeline)
        dep = datetime.fromisoformat(timeline["departure_utc"])
        now = dep + timedelta(minutes=31)

        session = AsyncMock()
        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "complete"
        assert trip.auto_completed is True

    @pytest.mark.asyncio
    async def test_force_close_at_departure_plus_24h(self):
        """Any state force-closes to complete at departure + 24h."""
        timeline = _make_timeline()
        trip = _make_trip(trip_status="en_route", projected_timeline=timeline)
        dep = datetime.fromisoformat(timeline["departure_utc"])
        now = dep + timedelta(hours=25)

        session = AsyncMock()
        await _advance_trip_state(trip, session, now)
        assert trip.trip_status == "complete"
        assert trip.auto_completed is True


# ---------------------------------------------------------------------------
# Interaction signals
# ---------------------------------------------------------------------------

class TestInteractionSignals:
    def test_signal_names(self):
        assert "timetogo_tap" in INTERACTION_SIGNALS
        assert "rideshare_tap" in INTERACTION_SIGNALS
        assert "nav_tap" in INTERACTION_SIGNALS


# ---------------------------------------------------------------------------
# Feedback timing
# ---------------------------------------------------------------------------

class TestFeedbackTiming:
    @pytest.mark.asyncio
    @patch("app.services.polling_agent.send_trip_notification", new_callable=AsyncMock)
    async def test_feedback_push_at_departure_plus_30(self, mock_notify):
        mock_notify.return_value = True
        timeline = _make_timeline()
        trip = _make_trip(trip_status="complete", projected_timeline=timeline, feedback_requested_at=None)
        dep = datetime.fromisoformat(timeline["departure_utc"])
        now = dep + timedelta(minutes=35)

        session = AsyncMock()
        await _handle_feedback_request(trip, session, now)
        mock_notify.assert_called_once()
        assert trip.feedback_requested_at == now

    @pytest.mark.asyncio
    @patch("app.services.polling_agent.send_trip_notification", new_callable=AsyncMock)
    async def test_feedback_not_sent_before_30_min(self, mock_notify):
        timeline = _make_timeline()
        trip = _make_trip(trip_status="complete", projected_timeline=timeline, feedback_requested_at=None)
        dep = datetime.fromisoformat(timeline["departure_utc"])
        now = dep + timedelta(minutes=20)

        session = AsyncMock()
        await _handle_feedback_request(trip, session, now)
        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.polling_agent.send_trip_notification", new_callable=AsyncMock)
    async def test_feedback_idempotent(self, mock_notify):
        """If feedback_requested_at is already set, don't send again."""
        timeline = _make_timeline()
        already_requested = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        trip = _make_trip(
            trip_status="complete",
            projected_timeline=timeline,
            feedback_requested_at=already_requested,
        )
        dep = datetime.fromisoformat(timeline["departure_utc"])
        now = dep + timedelta(hours=2)

        session = AsyncMock()
        await _handle_feedback_request(trip, session, now)
        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Phase-aware polling
# ---------------------------------------------------------------------------

class TestPhaseAwarePolling:
    def test_en_route_is_monitorable(self):
        assert "en_route" in MONITORABLE_STATUSES

    def test_at_airport_is_monitorable(self):
        assert "at_airport" in MONITORABLE_STATUSES

    def test_at_gate_is_monitorable(self):
        assert "at_gate" in MONITORABLE_STATUSES

    def test_complete_not_monitorable(self):
        assert "complete" not in MONITORABLE_STATUSES


# ---------------------------------------------------------------------------
# Projected timeline
# ---------------------------------------------------------------------------

class TestProjectedTimeline:
    def test_build_projected_timeline(self):
        response = MagicMock()
        response.leave_home_at = datetime(2026, 4, 5, 15, 0, tzinfo=timezone.utc)
        response.segments = [
            MagicMock(id="transport_drive", duration_minutes=45),
            MagicMock(id="at_airport", duration_minutes=5),
            MagicMock(id="tsa", duration_minutes=20),
            MagicMock(id="gate_walk", duration_minutes=10),
            MagicMock(id="gate_buffer", duration_minutes=30),
        ]

        tl = _build_projected_timeline(response, "2026-04-05T17:00:00+00:00")

        assert tl is not None
        assert "leave_home_at" in tl
        assert "arrive_airport_at" in tl
        assert "clear_security_at" in tl
        assert "departure_utc" in tl
        assert "computed_at" in tl

    def test_timeline_structure_on_track(self):
        """projected_timeline should have all required keys."""
        expected_keys = {"leave_home_at", "arrive_airport_at", "clear_security_at", "at_gate_at", "departure_utc", "computed_at"}
        tl = _make_timeline()
        assert set(tl.keys()) == expected_keys
