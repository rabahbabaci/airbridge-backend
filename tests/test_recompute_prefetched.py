"""Phase 2: recompute_recommendation respects prefetched_snapshot & edit-mode fallback."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.flight_snapshot import FlightSnapshot
from app.schemas.recommendations import RecommendationRecomputeRequest
from app.schemas.trips import TripContext, TripPreferences


def _context(trip_id="00000000-0000-0000-0000-000000000001") -> TripContext:
    return TripContext(
        trip_id=trip_id,
        input_mode="flight_number",
        flight_number="UA100",
        selected_departure_utc="2099-01-01 18:00Z",
        departure_date=date(2099, 1, 1),
        home_address="1 Market St",
        preferences=TripPreferences(),
        created_at=datetime.now(tz=timezone.utc),
    )


def _snapshot() -> FlightSnapshot:
    return FlightSnapshot(
        scheduled_departure=datetime(2099, 1, 1, 18, 0, 0, tzinfo=timezone.utc),
        departure_terminal="8",
        departure_gate="B14",
        origin_airport_code="SFO",
        departure_local_hour=10,
    )


class TestPrefetchedSnapshot:
    @pytest.mark.asyncio
    async def test_skips_adb_when_prefetched_provided(self):
        from app.services import recommendation_service

        ctx = _context()
        snap = _snapshot()

        async def _fake_build_response(trip_id, context, snapshot, now, user=None):
            # Prove the snapshot we provided is the one used downstream.
            from app.schemas.recommendations import RecommendationResponse, ConfidenceLevel
            return RecommendationResponse(
                trip_id=trip_id,
                leave_home_at=snapshot.scheduled_departure,
                confidence=ConfidenceLevel.medium,
                confidence_score=0.85,
                explanation="ok",
                segments=[],
                computed_at=now,
                origin_airport_code=snapshot.origin_airport_code,
            )

        build_mock = MagicMock(return_value=snap)
        with patch.object(recommendation_service, "get_trip_context", AsyncMock(return_value=ctx)), \
                patch.object(recommendation_service, "build_flight_snapshot", build_mock), \
                patch.object(recommendation_service, "_build_response", _fake_build_response):
            response = await recommendation_service.recompute_recommendation(
                RecommendationRecomputeRequest(trip_id=str(ctx.trip_id)),
                user=None,
                prefetched_snapshot=snap,
            )

        assert response is not None
        assert build_mock.call_count == 0  # ADB path bypassed

    @pytest.mark.asyncio
    async def test_falls_back_to_adb_when_no_prefetched(self):
        from app.services import recommendation_service

        ctx = _context()

        async def _fake_build_response(trip_id, context, snapshot, now, user=None):
            from app.schemas.recommendations import RecommendationResponse, ConfidenceLevel
            return RecommendationResponse(
                trip_id=trip_id,
                leave_home_at=snapshot.scheduled_departure,
                confidence=ConfidenceLevel.medium,
                confidence_score=0.85,
                explanation="ok",
                segments=[],
                computed_at=now,
                origin_airport_code="SFO",
            )

        build_mock = MagicMock(return_value=_snapshot())
        with patch.object(recommendation_service, "get_trip_context", AsyncMock(return_value=ctx)), \
                patch.object(recommendation_service, "build_flight_snapshot", build_mock), \
                patch.object(recommendation_service, "_build_response", _fake_build_response):
            response = await recommendation_service.recompute_recommendation(
                RecommendationRecomputeRequest(trip_id=str(ctx.trip_id)),
                user=None,
            )

        assert response is not None
        assert build_mock.call_count == 1

    @pytest.mark.asyncio
    async def test_edit_mode_overrides_force_fresh_adb(self):
        """If flight_number/date/selected_utc override is present, prefetched is ignored."""
        from app.services import recommendation_service

        ctx = _context()
        snap = _snapshot()

        async def _fake_build_response(trip_id, context, snapshot, now, user=None):
            from app.schemas.recommendations import RecommendationResponse, ConfidenceLevel
            return RecommendationResponse(
                trip_id=trip_id,
                leave_home_at=snapshot.scheduled_departure,
                confidence=ConfidenceLevel.medium,
                confidence_score=0.85,
                explanation="ok",
                segments=[],
                computed_at=now,
                origin_airport_code="SFO",
            )

        build_mock = MagicMock(return_value=snap)
        with patch.object(recommendation_service, "get_trip_context", AsyncMock(return_value=ctx)), \
                patch.object(recommendation_service, "build_flight_snapshot", build_mock), \
                patch.object(recommendation_service, "_build_response", _fake_build_response):
            await recommendation_service.recompute_recommendation(
                RecommendationRecomputeRequest(
                    trip_id=str(ctx.trip_id), flight_number="DL200"
                ),
                user=None,
                prefetched_snapshot=snap,
            )

        assert build_mock.call_count == 1  # override forced the fresh ADB path
