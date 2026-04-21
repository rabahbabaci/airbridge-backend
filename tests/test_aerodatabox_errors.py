"""Tests for differentiated AeroDataBox error handling.

Covers:
    * Integration layer: lookup_flights, lookup_airport_departures raising
      typed AeroDataBoxError subclasses per failure mode.
    * Route layer: /v1/flights/{n}/{date} and /v1/flights/search translating
      those to HTTP 404 / 503 with distinct error codes and Retry-After.
    * flight_snapshot_service.build_flight_snapshot strict=True vs False.
    * polling_agent.refresh_flight_status narrow exception + warning log.
"""

import uuid
from datetime import date as _date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.schemas.trips import TripContext, TripPreferences
from app.services.integrations.aerodatabox import (
    AeroDataBoxError,
    AeroDataBoxNotFound,
    AeroDataBoxRateLimited,
    AeroDataBoxTimeout,
    AeroDataBoxUnavailable,
    lookup_airport_departures,
    lookup_flights,
)


# ── Shared mock helpers ───────────────────────────────────────────────────────


class _MockResponse:
    def __init__(self, status_code: int, payload=None, raises_json: bool = False):
        self.status_code = status_code
        self._payload = payload
        self._raises_json = raises_json

    def json(self):
        if self._raises_json:
            raise ValueError("malformed JSON")
        return self._payload


def _patch_httpx_get(response_or_side_effect):
    """Return a context manager that patches aerodatabox.httpx.Client.get.

    Pass either a _MockResponse instance (returned from .get) or an exception
    instance (raised from .get).
    """
    patcher = patch("app.services.integrations.aerodatabox.httpx.Client")
    mock_cls = patcher.start()
    mock_cls.return_value.__enter__ = lambda s: s
    mock_cls.return_value.__exit__ = lambda s, *a: None

    if isinstance(response_or_side_effect, Exception):
        mock_cls.return_value.get = MagicMock(side_effect=response_or_side_effect)
    else:
        mock_cls.return_value.get = MagicMock(return_value=response_or_side_effect)
    return patcher


# ── Integration layer: lookup_flights ─────────────────────────────────────────


class TestLookupFlightsErrors:
    def test_404_raises_not_found(self):
        patcher = _patch_httpx_get(_MockResponse(404, payload=None))
        try:
            with pytest.raises(AeroDataBoxNotFound):
                lookup_flights("AA999", "2026-06-01")
        finally:
            patcher.stop()

    def test_429_raises_rate_limited(self):
        patcher = _patch_httpx_get(_MockResponse(429, payload=None))
        try:
            with pytest.raises(AeroDataBoxRateLimited):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_500_raises_unavailable(self):
        patcher = _patch_httpx_get(_MockResponse(500, payload=None))
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_503_raises_unavailable(self):
        patcher = _patch_httpx_get(_MockResponse(503, payload=None))
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_read_timeout_raises_timeout(self):
        patcher = _patch_httpx_get(httpx.ReadTimeout("read timed out"))
        try:
            with pytest.raises(AeroDataBoxTimeout):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_connect_error_raises_unavailable(self):
        patcher = _patch_httpx_get(httpx.ConnectError("connect refused"))
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_malformed_json_raises_unavailable(self):
        patcher = _patch_httpx_get(_MockResponse(200, raises_json=True))
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_non_list_response_raises_unavailable(self):
        """Upstream sometimes returns 200 with a dict (error body) instead of a list."""
        patcher = _patch_httpx_get(_MockResponse(200, payload={"error": "oops"}))
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_flights("AA123", "2026-06-01")
        finally:
            patcher.stop()

    def test_empty_list_returns_empty_no_exception(self):
        """A legitimate 200 + empty list is a valid "no matches" result, not an error."""
        patcher = _patch_httpx_get(_MockResponse(200, payload=[]))
        try:
            assert lookup_flights("AA123", "2026-06-01") == []
        finally:
            patcher.stop()

    def test_204_no_content_returns_empty_no_exception(self):
        """AeroDataBox returns 204 No Content when the flight number is not
        recognized. Per RFC 7231 that's a success response — treat it as an
        empty list, not an error. This mirrors the 200+empty-list behavior."""
        patcher = _patch_httpx_get(_MockResponse(204, payload=None))
        try:
            assert lookup_flights("UA99999", "2026-05-01") == []
        finally:
            patcher.stop()

    def test_204_does_not_attempt_json_parse(self):
        """HTTP 204 responses have an empty body by spec. Calling .json() on
        an empty body would raise JSONDecodeError. Verify lookup_flights
        short-circuits on status==204 before reaching the JSON parser."""

        class _ExplodingJsonResponse:
            status_code = 204

            def json(self):
                # If this method is ever called on a 204, that's a bug.
                raise AssertionError("lookup_flights should not call .json() on 204")

        patcher = _patch_httpx_get(_ExplodingJsonResponse())
        try:
            # Should return [] without ever hitting .json()
            assert lookup_flights("UA99999", "2026-05-01") == []
        finally:
            patcher.stop()


# ── Integration layer: lookup_airport_departures (two-window semantics) ──────


class TestLookupAirportDeparturesPartialSuccess:
    """Two-window partial-success semantics: raise only when both windows fail."""

    @staticmethod
    def _paired_windows(morning_response, afternoon_response):
        """Mock that returns different responses per T00:00 vs T12:00 window."""
        def _get(url, **kwargs):
            target = morning_response if "T00:00" in url else afternoon_response
            if isinstance(target, Exception):
                raise target
            return target
        return _get

    def _patch_paired(self, morning, afternoon):
        patcher = patch("app.services.integrations.aerodatabox.httpx.Client")
        mock_cls = patcher.start()
        mock_cls.return_value.__enter__ = lambda s: s
        mock_cls.return_value.__exit__ = lambda s, *a: None
        mock_cls.return_value.get = MagicMock(side_effect=self._paired_windows(morning, afternoon))
        return patcher

    _GOOD = _MockResponse(200, payload={"departures": []})

    def test_both_windows_fail_same_type_reraises(self):
        patcher = self._patch_paired(
            _MockResponse(500, payload=None),
            _MockResponse(500, payload=None),
        )
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_airport_departures("SFO", "2026-06-01")
        finally:
            patcher.stop()

    def test_partial_success_morning_window_only(self):
        """Window 1 succeeds, window 2 raises → return window 1 data, swallow error."""
        morning = _MockResponse(200, payload={"departures": [
            {
                "number": "UA300",
                "status": "Scheduled",
                "airline": {"name": "United"},
                "aircraft": {"model": "737"},
                "movement": {
                    "airport": {"iata": "LAX", "name": "LAX"},
                    "scheduledTime": {"utc": "2026-06-01 08:00Z", "local": "2026-06-01 08:00"},
                    "revisedTime": {},
                    "terminal": "1",
                    "gate": "A1",
                },
            },
        ]})
        patcher = self._patch_paired(morning, _MockResponse(503, payload=None))
        try:
            result = lookup_airport_departures("SFO", "2026-06-01")
            assert len(result) == 1
            assert result[0]["flight_number"] == "UA300"
        finally:
            patcher.stop()

    def test_partial_success_afternoon_window_only(self):
        """Window 1 raises, window 2 succeeds → return window 2 data."""
        afternoon = _MockResponse(200, payload={"departures": [
            {
                "number": "AA200",
                "status": "Scheduled",
                "airline": {"name": "American"},
                "aircraft": {"model": "737"},
                "movement": {
                    "airport": {"iata": "LAX", "name": "LAX"},
                    "scheduledTime": {"utc": "2026-06-01 19:00Z", "local": "2026-06-01 19:00"},
                    "revisedTime": {},
                    "terminal": "2",
                    "gate": "B1",
                },
            },
        ]})
        patcher = self._patch_paired(_MockResponse(503, payload=None), afternoon)
        try:
            result = lookup_airport_departures("SFO", "2026-06-01")
            assert len(result) == 1
            assert result[0]["flight_number"] == "AA200"
        finally:
            patcher.stop()

    def test_both_fail_different_severities_worst_wins(self):
        """Window 1 NotFound, Window 2 Unavailable → Unavailable is more severe, wins."""
        patcher = self._patch_paired(
            _MockResponse(404, payload=None),
            _MockResponse(500, payload=None),
        )
        try:
            with pytest.raises(AeroDataBoxUnavailable):
                lookup_airport_departures("XXX", "2026-06-01")
        finally:
            patcher.stop()

    def test_both_windows_204_returns_empty_no_exception(self):
        """Both departure windows returning 204 No Content (airport known but
        zero departures that day) should produce an empty list, not raise.
        Parallel to the 200+empty case — 204 is a success per RFC 7231."""
        patcher = self._patch_paired(
            _MockResponse(204, payload=None),
            _MockResponse(204, payload=None),
        )
        try:
            result = lookup_airport_departures("SFO", "2026-06-01")
            assert result == []
        finally:
            patcher.stop()


# ── Route layer: /v1/flights/{flight_number}/{date} ──────────────────────────


class TestFlightsByNumberRouteTranslation:
    """Route-layer tests mock at the flight_snapshot_service.lookup_flights
    layer because conftest.py's autouse fixture patches lookup_flights to
    return [] for all tests by default. The integration-layer tests above
    cover the HTTP-to-exception mapping; these cover exception-to-HTTP.
    """

    def test_upstream_503_returns_503_unavailable(self, client: TestClient):
        with patch(
            "app.services.flight_snapshot_service.lookup_flights",
            side_effect=AeroDataBoxUnavailable("503 from upstream"),
        ):
            resp = client.get("/v1/flights/AA123/2026-06-01")
        assert resp.status_code == 503
        assert resp.json()["code"] == "UPSTREAM_UNAVAILABLE"

    def test_upstream_429_returns_503_rate_limited_with_retry_after(self, client: TestClient):
        with patch(
            "app.services.flight_snapshot_service.lookup_flights",
            side_effect=AeroDataBoxRateLimited("429 from upstream"),
        ):
            resp = client.get("/v1/flights/AA123/2026-06-01")
        assert resp.status_code == 503
        assert resp.json()["code"] == "UPSTREAM_RATE_LIMITED"
        assert resp.headers.get("Retry-After") == "60"

    def test_upstream_404_returns_404(self, client: TestClient):
        with patch(
            "app.services.flight_snapshot_service.lookup_flights",
            side_effect=AeroDataBoxNotFound("flight does not exist"),
        ):
            resp = client.get("/v1/flights/XX999/2026-06-01")
        assert resp.status_code == 404
        assert "No flights found" in resp.json()["detail"]

    def test_upstream_timeout_returns_503(self, client: TestClient):
        with patch(
            "app.services.flight_snapshot_service.lookup_flights",
            side_effect=AeroDataBoxTimeout("timed out"),
        ):
            resp = client.get("/v1/flights/AA123/2026-06-01")
        assert resp.status_code == 503
        assert resp.json()["code"] == "UPSTREAM_UNAVAILABLE"

    def test_upstream_empty_list_returns_404(self, client: TestClient):
        """Default autouse patch returns [] → legit empty → 404 flight_not_found."""
        resp = client.get("/v1/flights/AA123/2026-06-01")
        assert resp.status_code == 404

    def test_upstream_204_end_to_end_returns_404(self, client: TestClient):
        """End-to-end proof that 204 from upstream maps to 404 at our route.

        Restores the real lookup_flights (conftest autouse stubs it to return
        [] by default) and mocks httpx to return 204. Verifies the new 204
        branch in lookup_flights returns [], which the route handler then
        converts to a 404 "No flights found" response.
        """
        from app.services.integrations import aerodatabox as adb_module

        # Override the autouse stub with the real function so the 204 branch
        # in lookup_flights actually runs.
        httpx_patcher = _patch_httpx_get(_MockResponse(204, payload=None))
        try:
            with patch(
                "app.services.flight_snapshot_service.lookup_flights",
                adb_module.lookup_flights,
            ):
                resp = client.get("/v1/flights/UA99999/2026-05-01")
        finally:
            httpx_patcher.stop()

        assert resp.status_code == 404
        assert "No flights found" in resp.json()["detail"]


# ── Route layer: /v1/flights/search ──────────────────────────────────────────


class TestFlightsSearchRouteTranslation:
    def _patch_both_windows(self, response_or_exc):
        """Both departure windows share one mock response/exception."""
        patcher = patch("app.services.integrations.aerodatabox.httpx.Client")
        mock_cls = patcher.start()
        mock_cls.return_value.__enter__ = lambda s: s
        mock_cls.return_value.__exit__ = lambda s, *a: None
        if isinstance(response_or_exc, Exception):
            mock_cls.return_value.get = MagicMock(side_effect=response_or_exc)
        else:
            mock_cls.return_value.get = MagicMock(return_value=response_or_exc)
        return patcher

    def test_both_windows_503_returns_503(self, client: TestClient):
        patcher = self._patch_both_windows(_MockResponse(503, payload=None))
        try:
            resp = client.get("/v1/flights/search", params={
                "origin": "SFO", "destination": "LAX", "date": "2026-06-01",
            })
            assert resp.status_code == 503
            assert resp.json()["code"] == "UPSTREAM_UNAVAILABLE"
        finally:
            patcher.stop()

    def test_both_windows_429_returns_503_rate_limited(self, client: TestClient):
        patcher = self._patch_both_windows(_MockResponse(429, payload=None))
        try:
            resp = client.get("/v1/flights/search", params={
                "origin": "SFO", "destination": "LAX", "date": "2026-06-01",
            })
            assert resp.status_code == 503
            assert resp.json()["code"] == "UPSTREAM_RATE_LIMITED"
            assert resp.headers.get("Retry-After") == "60"
        finally:
            patcher.stop()

    def test_legit_empty_day_returns_200_empty_list(self, client: TestClient):
        """Both windows return 200 + empty departures → legit empty-day, not 503."""
        patcher = self._patch_both_windows(_MockResponse(200, payload={"departures": []}))
        try:
            resp = client.get("/v1/flights/search", params={
                "origin": "SFO", "destination": "LAX", "date": "2026-06-01",
            })
            assert resp.status_code == 200
            assert resp.json() == {"flights": []}
        finally:
            patcher.stop()


# ── flight_snapshot_service: strict parameter ────────────────────────────────


def _make_trip_context(flight_number="AA123", date_str="2026-06-01"):
    return TripContext(
        trip_id=uuid.uuid4(),
        input_mode="flight_number",
        flight_number=flight_number,
        departure_date=_date.fromisoformat(date_str),
        home_address="123 Main St",
        created_at="2026-06-01T00:00:00+00:00",
        preferences=TripPreferences(),
    )


class TestBuildFlightSnapshotStrictMode:
    """The strict flag controls AeroDataBoxError handling; unexpected errors
    always fall back in both modes (hybrid behavior documented in the code)."""

    def test_strict_true_propagates_unavailable(self):
        from app.services import flight_snapshot_service

        with patch.object(
            flight_snapshot_service,
            "lookup_flights",
            side_effect=AeroDataBoxUnavailable("upstream down"),
        ):
            # Use a fresh cache key to avoid hitting the module-level cache
            ctx = _make_trip_context(flight_number="TESTSTRICT1")
            with pytest.raises(AeroDataBoxUnavailable):
                flight_snapshot_service.build_flight_snapshot(ctx, strict=True)

    def test_strict_true_propagates_rate_limited(self):
        from app.services import flight_snapshot_service

        with patch.object(
            flight_snapshot_service,
            "lookup_flights",
            side_effect=AeroDataBoxRateLimited("rate limited"),
        ):
            ctx = _make_trip_context(flight_number="TESTSTRICT2")
            with pytest.raises(AeroDataBoxRateLimited):
                flight_snapshot_service.build_flight_snapshot(ctx, strict=True)

    def test_strict_false_falls_back_on_adb_error(self):
        from app.services import flight_snapshot_service

        with patch.object(
            flight_snapshot_service,
            "lookup_flights",
            side_effect=AeroDataBoxUnavailable("upstream down"),
        ):
            ctx = _make_trip_context(flight_number="TESTSTRICT3")
            snapshot = flight_snapshot_service.build_flight_snapshot(ctx, strict=False)
            assert snapshot is not None
            # Fallback snapshot has the canonical 10 AM UTC scheduled_departure
            assert snapshot.scheduled_departure.hour == 10
            assert snapshot.departure_local_hour == 10

    def test_strict_true_unexpected_exception_still_falls_back(self):
        """Hybrid behavior: typed AeroDataBoxError propagates; random exceptions
        are still caught by the outer except Exception and fall back."""
        from app.services import flight_snapshot_service

        with patch.object(
            flight_snapshot_service,
            "lookup_flights",
            side_effect=KeyError("unexpected"),
        ):
            ctx = _make_trip_context(flight_number="TESTSTRICT4")
            # Should NOT raise — outer except Exception handles it.
            snapshot = flight_snapshot_service.build_flight_snapshot(ctx, strict=True)
            assert snapshot is not None
            assert snapshot.scheduled_departure.hour == 10


# ── polling_agent: narrow exception + warning log ────────────────────────────


class TestPollingAgentNarrowsAdbError:
    @pytest.mark.asyncio
    async def test_refresh_flight_status_catches_adb_error_logs_class_name(self, caplog):
        from app.services import polling_agent

        trip_row = MagicMock()
        trip_row.id = uuid.uuid4()
        trip_row.flight_number = "AA123"
        trip_row.departure_date = "2026-06-01"

        with patch.object(
            polling_agent,
            "lookup_flights",
            side_effect=AeroDataBoxTimeout("timed out"),
        ):
            import logging
            with caplog.at_level(logging.WARNING, logger="app.services.polling_agent"):
                was_called, changes = await polling_agent.refresh_flight_status(trip_row, session=None)

        assert was_called is False
        assert changes == {}
        # Warning includes the exception class name for rehearsal visibility
        assert any("AeroDataBoxTimeout" in rec.message for rec in caplog.records)
