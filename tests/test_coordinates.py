"""Tests for terminal_coordinates lookup, geocode_address, and response schema fields."""

from unittest.mock import patch, MagicMock

import pytest

from app.services.integrations import google_maps
from app.schemas.recommendations import RecommendationResponse


class TestGetTerminalCoordinates:
    """Tests for get_terminal_coordinates static lookup and geocoding fallback."""

    def setup_method(self):
        # Reset the module-level cache so each test loads fresh data
        google_maps._terminal_coords_cache = None

    def test_known_airport_and_terminal(self):
        """Known airport + terminal returns coordinates from static file."""
        result = google_maps.get_terminal_coordinates("SFO", "1")
        assert result is not None
        assert "lat" in result
        assert "lng" in result

    def test_unknown_terminal_falls_back_to_default(self):
        """Unknown terminal for a known airport falls back to 'default'."""
        result = google_maps.get_terminal_coordinates("SFO", "ZZZ")
        default = google_maps.get_terminal_coordinates("SFO", None)
        assert result is not None
        assert result == default

    def test_unknown_airport_no_destination_returns_none(self):
        """Airport not in static file AND not in AIRPORT_DESTINATIONS returns None."""
        result = google_maps.get_terminal_coordinates("XXX", "1")
        assert result is None

    def test_geocoding_fallback_for_missing_airport(self):
        """Airport not in static file but in AIRPORT_DESTINATIONS triggers geocoding API."""
        # Temporarily remove SFO from static coords to simulate a missing airport
        google_maps._terminal_coords_cache = None
        coords = google_maps._load_terminal_coords()
        saved = coords.pop("SFO")

        fake_response = MagicMock()
        fake_response.json.return_value = {
            "results": [{"geometry": {"location": {"lat": 37.621, "lng": -122.379}}}]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("app.services.integrations.google_maps.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_instance.get.return_value = fake_response

            result = google_maps.get_terminal_coordinates("SFO", "1")

        assert result == {"lat": 37.621, "lng": -122.379}
        mock_instance.get.assert_called_once()

        # Restore
        coords["SFO"] = saved


class TestGeocodeAddress:
    """Tests for geocode_address with caching."""

    def setup_method(self):
        google_maps._geocode_cache.clear()

    def test_geocode_returns_lat_lng(self):
        """geocode_address returns lat/lng dict on success."""
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "results": [{"geometry": {"location": {"lat": 37.7749, "lng": -122.4194}}}]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("app.services.integrations.google_maps.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_instance.get.return_value = fake_response

            result = google_maps.geocode_address("123 Main St, SF, CA")

        assert result == {"lat": 37.7749, "lng": -122.4194}

    def test_geocode_caching(self):
        """Second call with same address uses cache — no HTTP request."""
        fake_response = MagicMock()
        fake_response.json.return_value = {
            "results": [{"geometry": {"location": {"lat": 1.0, "lng": 2.0}}}]
        }
        fake_response.raise_for_status = MagicMock()

        with patch("app.services.integrations.google_maps.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_instance.get.return_value = fake_response

            result1 = google_maps.geocode_address("456 Oak Ave")
            result2 = google_maps.geocode_address("456 Oak Ave")

        assert result1 == result2 == {"lat": 1.0, "lng": 2.0}
        # HTTP client should only be constructed once (first call)
        assert mock_client.call_count == 1


class TestRecommendationResponseSchema:
    """Tests that new fields exist on RecommendationResponse with correct defaults."""

    def test_new_fields_default_to_none(self):
        """terminal_coordinates and home_coordinates default to None."""
        resp = RecommendationResponse(
            trip_id="t1",
            leave_home_at="2025-01-01T10:00:00Z",
            confidence="medium",
            confidence_score=0.85,
            explanation="test",
            computed_at="2025-01-01T09:00:00Z",
        )
        assert resp.terminal_coordinates is None
        assert resp.home_coordinates is None

    def test_new_fields_accept_values(self):
        """terminal_coordinates and home_coordinates accept dict values."""
        resp = RecommendationResponse(
            trip_id="t1",
            leave_home_at="2025-01-01T10:00:00Z",
            confidence="medium",
            confidence_score=0.85,
            explanation="test",
            computed_at="2025-01-01T09:00:00Z",
            terminal_coordinates={"lat": 37.0, "lng": -122.0},
            home_coordinates={"lat": 38.0, "lng": -121.0},
        )
        assert resp.terminal_coordinates == {"lat": 37.0, "lng": -122.0}
        assert resp.home_coordinates == {"lat": 38.0, "lng": -121.0}
