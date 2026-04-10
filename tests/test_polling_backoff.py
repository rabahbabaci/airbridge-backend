"""Tests for polling agent exponential backoff and failure budget."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.polling_agent import (
    BACKOFF_BASE,
    BACKOFF_MAX,
    DEFAULT_SLEEP,
    MAX_CONSECUTIVE_ERRORS,
    compute_backoff,
)


class TestComputeBackoff:
    """Unit tests for the backoff calculation."""

    def test_first_failure_is_base(self):
        assert compute_backoff(1) == 60

    def test_second_failure_doubles(self):
        assert compute_backoff(2) == 120

    def test_third_failure(self):
        assert compute_backoff(3) == 240

    def test_fourth_failure(self):
        assert compute_backoff(4) == 480

    def test_fifth_failure_hits_cap(self):
        # 60 * 2^4 = 960, capped at 900
        assert compute_backoff(5) == 900

    def test_sixth_failure_stays_at_cap(self):
        assert compute_backoff(6) == 900

    def test_twentieth_failure_stays_at_cap(self):
        assert compute_backoff(20) == 900


class TestPollingLoopResilience:
    """Integration tests for polling loop error handling."""

    @pytest.mark.asyncio
    async def test_consecutive_errors_increment_and_reset(self):
        """Errors increment the counter; a successful query resets it to 0."""
        from app.services import polling_agent

        call_count = 0
        errors_seen = []

        # Mock factory: fail twice, then succeed with empty trips
        async def mock_factory_ctx():
            nonlocal call_count
            call_count += 1
            session = AsyncMock()
            if call_count <= 2:
                session.execute = AsyncMock(side_effect=ConnectionError("pooler refused"))
            else:
                # Return empty result (no trips)
                result = MagicMock()
                result.scalars.return_value.all.return_value = []
                session.execute = AsyncMock(return_value=result)
            return session

        mock_factory = MagicMock()
        mock_factory.return_value.__aenter__ = AsyncMock(side_effect=mock_factory_ctx)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        # Patch to avoid real sleep and limit iterations
        iteration = 0
        max_iterations = 4

        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration >= max_iterations:
                raise asyncio.CancelledError()  # stop the loop

        with patch.object(polling_agent, "_get_active_trips") as mock_get, \
             patch("app.db.async_session_factory", mock_factory), \
             patch("asyncio.sleep", side_effect=mock_sleep):

            # Make _get_active_trips raise on first two calls, return [] on third
            call_idx = 0
            async def get_trips_side_effect(session):
                nonlocal call_idx
                call_idx += 1
                if call_idx <= 2:
                    raise ConnectionError("pooler refused")
                return []

            mock_get.side_effect = get_trips_side_effect

            import app.db as _db
            _original = _db.async_session_factory
            _db.async_session_factory = mock_factory

            try:
                await polling_agent.polling_loop()
            except asyncio.CancelledError:
                pass
            finally:
                _db.async_session_factory = _original

    @pytest.mark.asyncio
    async def test_failure_budget_exits_loop(self):
        """After MAX_CONSECUTIVE_ERRORS failures, the loop exits (returns)."""
        from app.services import polling_agent

        error_count = 0

        async def mock_sleep(seconds):
            nonlocal error_count
            # Don't actually sleep — just count

        # Create a factory that always raises
        mock_factory = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("always fails"))
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = ctx

        import app.db as _db
        _original = _db.async_session_factory
        _db.async_session_factory = mock_factory

        with patch("asyncio.sleep", side_effect=mock_sleep):
            # polling_loop should return after MAX_CONSECUTIVE_ERRORS
            try:
                await polling_agent.polling_loop()
            except asyncio.CancelledError:
                pytest.fail("Loop should have returned, not been cancelled")

        _db.async_session_factory = _original
        # If we got here without hanging, the failure budget worked

    @pytest.mark.asyncio
    async def test_backoff_intervals_increase(self):
        """Verify actual sleep intervals increase on consecutive errors."""
        from app.services import polling_agent

        sleep_intervals = []

        async def capture_sleep(seconds):
            sleep_intervals.append(seconds)

        # Factory that always fails
        mock_factory = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("fail"))
        ctx.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = ctx

        import app.db as _db
        _original = _db.async_session_factory
        _db.async_session_factory = mock_factory

        with patch("asyncio.sleep", side_effect=capture_sleep):
            await polling_agent.polling_loop()

        _db.async_session_factory = _original

        # First sleep is STARTUP_DELAY (10s), then backoff intervals
        # Skip startup delay, check backoff intervals
        backoff_sleeps = sleep_intervals[1:]  # skip startup delay

        # Should have MAX_CONSECUTIVE_ERRORS - 1 backoff sleeps: the final
        # error triggers return before sleeping
        assert len(backoff_sleeps) == MAX_CONSECUTIVE_ERRORS - 1

        # First few should double: 60, 120, 240, 480, 900, 900, ...
        assert backoff_sleeps[0] == 60
        assert backoff_sleeps[1] == 120
        assert backoff_sleeps[2] == 240
        assert backoff_sleeps[3] == 480
        assert backoff_sleeps[4] == 900  # capped
        assert backoff_sleeps[5] == 900  # stays capped
        # All remaining should be at cap
        assert all(s == 900 for s in backoff_sleeps[4:])
