"""Phase 2 polling agent tests: A/B paths, status refresh, gate/cancellation pushes."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _future_dep(hours: float) -> datetime:
    return datetime.now(tz=timezone.utc) + timedelta(hours=hours)


def _make_trip(
    *,
    trip_status: str = "active",
    flight_info: dict | None = None,
    flight_status: dict | None = None,
    dep_hours_out: float = 24,
    user_is_pro: bool = True,
) -> MagicMock:
    """Build a MagicMock trip wired up for _process_trip exercises."""
    dep = _future_dep(dep_hours_out)
    trip = MagicMock()
    trip.id = "trip-xyz"
    trip.user_id = "user-abc"
    trip.input_mode = "flight_number"
    trip.flight_number = "UA100"
    trip.departure_date = dep.date().isoformat()
    trip.selected_departure_utc = dep.isoformat()
    trip.status = trip_status
    trip.trip_status = trip_status
    trip.flight_info = flight_info
    trip.flight_status = flight_status
    trip.preferences_json = None
    trip.push_count = 0
    trip.last_pushed_leave_home_at = None
    trip.morning_email_sent_at = None
    trip.time_to_go_push_sent_at = None
    trip.sms_count = 0
    trip.auto_completed = False
    trip.feedback_requested_at = None

    # projected_timeline lets _advance_trip_state evaluate without advancing
    future_leave = (dep - timedelta(hours=3)).isoformat()
    future_arrive = (dep - timedelta(hours=2)).isoformat()
    future_security = (dep - timedelta(hours=1, minutes=30)).isoformat()
    future_gate = (dep - timedelta(hours=1)).isoformat()
    trip.projected_timeline = {
        "leave_home_at": future_leave,
        "arrive_airport_at": future_arrive,
        "clear_security_at": future_security,
        "at_gate_at": future_gate,
        "departure_utc": dep.isoformat(),
    }

    user = MagicMock()
    user.id = trip.user_id
    user.email = "test@example.com"
    user.phone_number = "+1"
    user.trip_count = 1 if user_is_pro else 10
    user.subscription_status = "active" if user_is_pro else "none"
    trip.user = user

    return trip


def _fresh_flight(
    *,
    gate: str | None = "B10",
    status: str = "Scheduled",
    terminal: str | None = "8",
    revised_utc: str | None = None,
) -> dict:
    return {
        "flight_number": "UA100",
        "airline_name": "United Airlines",
        "origin_iata": "SFO",
        "destination_iata": "ORD",
        "departure_time_utc": "2099-01-01 18:00Z",
        "departure_time_local": "2099-01-01 10:00",
        "arrival_time_utc": "2099-01-01 22:00Z",
        "arrival_time_local": "2099-01-01 14:00",
        "revised_departure_utc": revised_utc,
        "revised_departure_local": None,
        "departure_terminal": terminal,
        "departure_gate": gate,
        "arrival_terminal": "1",
        "status": status,
        "is_delayed": bool(revised_utc),
        "aircraft_model": "B737",
    }


def _stored_flight_info(
    terminal: str = "8",
    origin: str = "SFO",
    local_hour: int = 10,
) -> dict:
    return {
        "airline": "United Airlines",
        "flight_number": "UA100",
        "origin_iata": origin,
        "destination_iata": "ORD",
        "scheduled_departure_at": "2099-01-01T18:00:00+00:00",
        "scheduled_arrival_at": "2099-01-01T22:00:00+00:00",
        "aircraft_type": "B737",
        "terminal": terminal,
        "duration_minutes": 240,
        "departure_local_hour": local_hour,
        "snapshot_taken_at": "2026-04-01T12:00:00+00:00",
    }


def _stored_flight_status(
    gate: str | None = "B10",
    status: str = "Scheduled",
    cancelled: bool = False,
    last_updated: datetime | None = None,
) -> dict:
    if last_updated is None:
        last_updated = datetime.now(tz=timezone.utc)
    return {
        "gate": gate,
        "status": status,
        "delay_minutes": 0,
        "actual_departure_at": None,
        "cancelled": cancelled,
        "last_updated_at": last_updated.isoformat(),
    }


# ---------------------------------------------------------------------------
# _should_skip_refresh (Path B predicate)
# ---------------------------------------------------------------------------

class TestShouldSkipRefresh:
    def test_skip_when_all_conditions_met(self):
        from app.services.polling_agent import _should_skip_refresh

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_status=_stored_flight_status(last_updated=now - timedelta(minutes=5)),
            dep_hours_out=24,
        )
        assert _should_skip_refresh(trip, secs_to_dep=24 * 3600, now=now) is True

    def test_no_skip_within_final_30_minutes(self):
        from app.services.polling_agent import _should_skip_refresh

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_status=_stored_flight_status(last_updated=now - timedelta(seconds=1)),
            dep_hours_out=0.3,
        )
        assert _should_skip_refresh(trip, secs_to_dep=1000, now=now) is False

    def test_no_skip_when_cancelled(self):
        from app.services.polling_agent import _should_skip_refresh

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_status=_stored_flight_status(
                cancelled=True, last_updated=now - timedelta(minutes=1)
            ),
        )
        assert _should_skip_refresh(trip, secs_to_dep=24 * 3600, now=now) is False

    def test_no_skip_when_last_updated_stale(self):
        from app.services.polling_agent import _should_skip_refresh

        now = datetime.now(tz=timezone.utc)
        # 30-min interval for >6h-out trips; stale by 2 hours
        trip = _make_trip(
            flight_status=_stored_flight_status(last_updated=now - timedelta(hours=2)),
        )
        assert _should_skip_refresh(trip, secs_to_dep=24 * 3600, now=now) is False

    def test_no_skip_when_flight_status_missing(self):
        from app.services.polling_agent import _should_skip_refresh

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(flight_status=None)
        assert _should_skip_refresh(trip, secs_to_dep=24 * 3600, now=now) is False


# ---------------------------------------------------------------------------
# refresh_flight_status (Path A)
# ---------------------------------------------------------------------------

class TestRefreshFlightStatus:
    @pytest.mark.asyncio
    async def test_writes_flight_status_and_returns_changes(self):
        from app.services.polling_agent import refresh_flight_status

        trip = _make_trip(
            flight_info=_stored_flight_info(terminal="8"),
            flight_status=_stored_flight_status(gate="B10", status="Scheduled"),
        )
        session = AsyncMock()

        with patch(
            "app.services.polling_agent.lookup_flights",
            return_value=[_fresh_flight(gate="B14", status="Scheduled", terminal="8")],
        ):
            was_called, changes = await refresh_flight_status(trip, session)

        assert was_called is True
        assert changes == {"gate": ("B10", "B14")}
        assert trip.flight_status["gate"] == "B14"
        # flight_info unchanged (same terminal, same everything else)
        assert trip.flight_info["terminal"] == "8"
        assert trip.flight_info["snapshot_taken_at"] == "2026-04-01T12:00:00+00:00"

    @pytest.mark.asyncio
    async def test_detects_terminal_change_updates_only_terminal(self, caplog):
        import logging
        from app.services.polling_agent import refresh_flight_status

        trip = _make_trip(
            flight_info=_stored_flight_info(terminal="8"),
            flight_status=_stored_flight_status(gate="B10"),
        )
        session = AsyncMock()

        with patch(
            "app.services.polling_agent.lookup_flights",
            return_value=[_fresh_flight(terminal="3")],
        ), caplog.at_level(logging.INFO, logger="app.services.polling_agent"):
            was_called, changes = await refresh_flight_status(trip, session)

        assert was_called is True
        assert "terminal" in changes
        assert changes["terminal"] == ("8", "3")
        # Only terminal changed inside flight_info — other frozen fields intact
        assert trip.flight_info["terminal"] == "3"
        assert trip.flight_info["airline"] == "United Airlines"
        assert trip.flight_info["snapshot_taken_at"] == "2026-04-01T12:00:00+00:00"
        assert trip.flight_info["duration_minutes"] == 240
        assert any("terminal changed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_returns_false_when_adb_empty(self):
        from app.services.polling_agent import refresh_flight_status

        trip = _make_trip(
            flight_info=_stored_flight_info(),
            flight_status=_stored_flight_status(),
        )
        with patch("app.services.polling_agent.lookup_flights", return_value=[]):
            was_called, changes = await refresh_flight_status(trip, AsyncMock())
        assert was_called is False
        assert changes == {}

    @pytest.mark.asyncio
    async def test_handles_adb_exception(self):
        from app.services.polling_agent import refresh_flight_status

        trip = _make_trip()
        with patch(
            "app.services.polling_agent.lookup_flights",
            side_effect=Exception("network"),
        ):
            was_called, changes = await refresh_flight_status(trip, AsyncMock())
        assert was_called is False
        assert changes == {}


# ---------------------------------------------------------------------------
# _process_trip — end-to-end Path A/B behavior
# ---------------------------------------------------------------------------

def _patch_process_trip_dependencies(
    lookup_flights_return=None,
    lookup_flights_side_effect=None,
    recompute_return=None,
):
    """Produce a patcher stack that isolates _process_trip from DB / events / recompute."""
    patches = [
        patch(
            "app.services.polling_agent.lookup_flights",
            return_value=lookup_flights_return or [],
            side_effect=lookup_flights_side_effect,
        ),
        patch(
            "app.services.polling_agent._check_interaction_signals",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.polling_agent.recompute_recommendation",
            new=AsyncMock(return_value=recompute_return),
        ),
        patch(
            "app.services.polling_agent.send_trip_notification",
            new=AsyncMock(return_value=True),
        ),
    ]
    return patches


def _apply_patches(patches):
    started = [p.start() for p in patches]
    return started, patches


def _stop_patches(patches):
    for p in patches:
        p.stop()


class TestProcessTripPathAB:
    @pytest.mark.asyncio
    async def test_path_b_skips_lookup_flights_when_conditions_met(self):
        """Far-out trip, recent flight_status, stable status → no ADB call."""
        from app.services.polling_agent import _process_trip

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_info=_stored_flight_info(),
            flight_status=_stored_flight_status(last_updated=now - timedelta(minutes=5)),
            dep_hours_out=24,
        )
        session = AsyncMock()
        patches = _patch_process_trip_dependencies()
        started, _ = _apply_patches(patches)
        mock_lookup = started[0]
        try:
            await _process_trip(trip, session)
        finally:
            _stop_patches(patches)

        assert mock_lookup.call_count == 0

    @pytest.mark.asyncio
    async def test_path_a_refreshes_flight_status_and_preserves_flight_info(self):
        """Stale last_updated → Path A refreshes flight_status, leaves flight_info alone."""
        from app.services.polling_agent import _process_trip

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_info=_stored_flight_info(terminal="8"),
            flight_status=_stored_flight_status(
                gate="B10", last_updated=now - timedelta(hours=2)
            ),
            dep_hours_out=24,
        )
        session = AsyncMock()
        patches = _patch_process_trip_dependencies(
            lookup_flights_return=[_fresh_flight(gate="B14", terminal="8")],
        )
        started, _ = _apply_patches(patches)
        try:
            await _process_trip(trip, session)
        finally:
            _stop_patches(patches)

        assert trip.flight_status["gate"] == "B14"
        # flight_info unchanged
        assert trip.flight_info["terminal"] == "8"
        assert trip.flight_info["airline"] == "United Airlines"
        assert trip.flight_info["snapshot_taken_at"] == "2026-04-01T12:00:00+00:00"

    @pytest.mark.asyncio
    async def test_at_gate_phase_refreshes_status_but_does_not_recompute(self):
        from app.services.polling_agent import _process_trip

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            trip_status="at_gate",
            flight_info=_stored_flight_info(),
            flight_status=_stored_flight_status(
                gate="B10", last_updated=now - timedelta(hours=2)
            ),
            dep_hours_out=0.4,  # ~24 min out — in final-30-min window
        )
        session = AsyncMock()
        patches = _patch_process_trip_dependencies(
            lookup_flights_return=[_fresh_flight(gate="B20")],
        )
        started, _ = _apply_patches(patches)
        mock_lookup = started[0]
        mock_recompute = started[2]
        try:
            await _process_trip(trip, session)
        finally:
            _stop_patches(patches)

        assert mock_lookup.call_count == 1  # Path A was taken
        assert mock_recompute.await_count == 0  # no recompute for at_gate
        assert trip.flight_status["gate"] == "B20"

    @pytest.mark.asyncio
    async def test_final_30_minutes_forces_path_a(self):
        """Even with recent last_updated, <30 min to dep forces Path A."""
        from app.services.polling_agent import _process_trip

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_info=_stored_flight_info(),
            flight_status=_stored_flight_status(
                last_updated=now - timedelta(seconds=10),
            ),
            dep_hours_out=0.25,  # 15 minutes out
        )
        session = AsyncMock()
        patches = _patch_process_trip_dependencies(
            lookup_flights_return=[_fresh_flight()],
            recompute_return=None,
        )
        started, _ = _apply_patches(patches)
        mock_lookup = started[0]
        try:
            await _process_trip(trip, session)
        finally:
            _stop_patches(patches)

        assert mock_lookup.call_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_adb_when_flight_info_missing(self, caplog):
        """Defensive: recompute still runs with ADB path when flight_info is None."""
        import logging
        from app.services.polling_agent import _process_trip

        trip = _make_trip(
            flight_info=None,
            flight_status=None,
            dep_hours_out=5,
        )
        session = AsyncMock()

        recompute_mock = AsyncMock(return_value=None)
        patches = [
            patch(
                "app.services.polling_agent.lookup_flights",
                return_value=[_fresh_flight()],
            ),
            patch(
                "app.services.polling_agent._check_interaction_signals",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.polling_agent.recompute_recommendation",
                new=recompute_mock,
            ),
            patch(
                "app.services.polling_agent.send_trip_notification",
                new=AsyncMock(return_value=True),
            ),
        ]
        for p in patches:
            p.start()
        try:
            with caplog.at_level(logging.WARNING, logger="app.services.polling_agent"):
                await _process_trip(trip, session)
        finally:
            for p in patches:
                p.stop()

        # recompute was called with prefetched_snapshot=None (no flight_info to read)
        assert recompute_mock.await_count == 1
        call_kwargs = recompute_mock.await_args.kwargs
        assert call_kwargs.get("prefetched_snapshot") is None


class TestProcessTripNotifications:
    @pytest.mark.asyncio
    async def test_fires_gate_change_push_when_gate_changes(self):
        from app.services.polling_agent import _process_trip

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            trip_status="at_airport",
            flight_info=_stored_flight_info(),
            flight_status=_stored_flight_status(
                gate="B10", last_updated=now - timedelta(hours=2)
            ),
            dep_hours_out=1.0,
        )
        session = AsyncMock()
        patches = _patch_process_trip_dependencies(
            lookup_flights_return=[_fresh_flight(gate="B14")],
        )
        started, _ = _apply_patches(patches)
        mock_notify = started[3]
        try:
            await _process_trip(trip, session)
        finally:
            _stop_patches(patches)

        assert mock_notify.await_count >= 1
        kinds = {call.kwargs["notification_type"] for call in mock_notify.await_args_list}
        assert "gate_change" in kinds

    @pytest.mark.asyncio
    async def test_fires_cancellation_push_when_cancelled(self):
        from app.services.polling_agent import _process_trip

        now = datetime.now(tz=timezone.utc)
        trip = _make_trip(
            flight_info=_stored_flight_info(),
            flight_status=_stored_flight_status(
                status="Scheduled", cancelled=False, last_updated=now - timedelta(hours=2)
            ),
            dep_hours_out=5.0,
        )
        session = AsyncMock()
        patches = _patch_process_trip_dependencies(
            lookup_flights_return=[_fresh_flight(status="Cancelled")],
        )
        started, _ = _apply_patches(patches)
        mock_notify = started[3]
        try:
            await _process_trip(trip, session)
        finally:
            _stop_patches(patches)

        kinds = {call.kwargs["notification_type"] for call in mock_notify.await_args_list}
        assert "cancellation" in kinds
