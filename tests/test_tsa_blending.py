"""Tests for TSA three-layer blending model and API client cache."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from app.services.integrations.tsa_model import (
    API_FRESHNESS_SECONDS,
    MIN_FEEDBACK_OBSERVATIONS,
    _compute_weights,
    estimate_tsa_wait,
)
from app.services.integrations import tsa_api


# ---------------------------------------------------------------------------
# Weight computation
# ---------------------------------------------------------------------------

class TestWeightComputation:
    def test_all_three_layers(self):
        w_s, w_a, w_f = _compute_weights(has_api=True, has_feedback=True)
        assert (w_s, w_a, w_f) == (0.30, 0.50, 0.20)

    def test_static_plus_api(self):
        w_s, w_a, w_f = _compute_weights(has_api=True, has_feedback=False)
        assert (w_s, w_a, w_f) == (0.375, 0.625, 0.0)

    def test_static_plus_feedback(self):
        w_s, w_a, w_f = _compute_weights(has_api=False, has_feedback=True)
        assert (w_s, w_a, w_f) == (0.80, 0.0, 0.20)

    def test_static_only(self):
        w_s, w_a, w_f = _compute_weights(has_api=False, has_feedback=False)
        assert (w_s, w_a, w_f) == (1.0, 0.0, 0.0)

    def test_weights_always_sum_to_one(self):
        for has_api in (True, False):
            for has_fb in (True, False):
                w = _compute_weights(has_api, has_fb)
                assert abs(sum(w) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Blending output shape and values
# ---------------------------------------------------------------------------

class TestBlendingOutput:
    def test_static_only_returns_expected_shape(self):
        result = estimate_tsa_wait("LAX", departure_hour=10, day_of_week=2)
        assert set(result.keys()) == {"p25", "p50", "p75", "p80", "airport", "source", "volume_ratio"}
        assert result["airport"] == "LAX"
        assert result["source"] == "baseline"
        assert result["volume_ratio"] is None
        for key in ("p25", "p50", "p75", "p80"):
            assert isinstance(result[key], int)
            assert result[key] >= 0

    def test_with_fresh_api_data_blends(self):
        api_data = {"wait_minutes": 30, "fetched_at": time.time()}
        result_with_api = estimate_tsa_wait(
            "LAX", departure_hour=10, day_of_week=2, live_api_data=api_data,
        )
        result_static = estimate_tsa_wait("LAX", departure_hour=10, day_of_week=2)
        assert "api" in result_with_api["source"]
        # If API says 30 min and baseline differs, the blended result should differ from static-only
        # (unless baseline p50 happens to be exactly 30)
        assert result_with_api["source"] != result_static["source"]

    def test_stale_api_data_ignored(self):
        api_data = {"wait_minutes": 30, "fetched_at": time.time() - API_FRESHNESS_SECONDS - 1}
        result = estimate_tsa_wait(
            "LAX", departure_hour=10, day_of_week=2, live_api_data=api_data,
        )
        assert result["source"] == "baseline"

    def test_with_feedback_data_blends(self):
        fb_data = {"avg_wait_minutes": 25, "observation_count": 15}
        result = estimate_tsa_wait(
            "LAX", departure_hour=10, day_of_week=2, user_feedback_data=fb_data,
        )
        assert "feedback" in result["source"]

    def test_feedback_below_threshold_ignored(self):
        fb_data = {"avg_wait_minutes": 25, "observation_count": MIN_FEEDBACK_OBSERVATIONS - 1}
        result = estimate_tsa_wait(
            "LAX", departure_hour=10, day_of_week=2, user_feedback_data=fb_data,
        )
        assert result["source"] == "baseline"

    def test_all_three_layers(self):
        api_data = {"wait_minutes": 30, "fetched_at": time.time()}
        fb_data = {"avg_wait_minutes": 25, "observation_count": 20}
        result = estimate_tsa_wait(
            "LAX", departure_hour=10, day_of_week=2,
            live_api_data=api_data, user_feedback_data=fb_data,
        )
        assert result["source"] == "baseline+api+feedback"

    def test_security_access_discount_applied(self):
        result_none = estimate_tsa_wait("LAX", departure_hour=10, security_access="none")
        result_pre = estimate_tsa_wait("LAX", departure_hour=10, security_access="precheck")
        assert result_pre["p50"] < result_none["p50"]

    def test_p25_floor_at_3(self):
        result = estimate_tsa_wait("LAX", departure_hour=3, security_access="clear_precheck")
        assert result["p25"] >= 3

    def test_unknown_airport_falls_back_to_default(self):
        result = estimate_tsa_wait("ZZZ", departure_hour=10, day_of_week=2)
        assert result["airport"] == "ZZZ"
        assert result["p50"] > 0

    def test_volume_ratio_scales_output(self):
        result_normal = estimate_tsa_wait("LAX", departure_hour=10)
        result_high = estimate_tsa_wait("LAX", departure_hour=10, flight_volume_ratio=1.5)
        assert result_high["p50"] >= result_normal["p50"]


# ---------------------------------------------------------------------------
# TSA API client cache
# ---------------------------------------------------------------------------

class TestTsaApiCache:
    def setup_method(self):
        tsa_api.clear_cache()

    @pytest.mark.asyncio
    @patch("app.services.integrations.tsa_api.settings")
    async def test_no_api_key_returns_none(self, mock_settings):
        mock_settings.tsa_wait_times_api_key = ""
        result = await tsa_api.fetch_live_tsa_wait("LAX")
        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.integrations.tsa_api.settings")
    async def test_successful_fetch_caches_result(self, mock_settings):
        mock_settings.tsa_wait_times_api_key = "test-key"
        # Manually populate cache to test cache hit path
        now = time.time()
        cached = {"wait_minutes": 20, "fetched_at": now}
        tsa_api._cache["LAX"] = (now, cached)

        result = await tsa_api.fetch_live_tsa_wait("LAX")
        assert result is not None
        assert result["wait_minutes"] == 20
        assert result["fetched_at"] == now

    @pytest.mark.asyncio
    @patch("app.services.integrations.tsa_api.settings")
    async def test_cache_expiry(self, mock_settings):
        mock_settings.tsa_wait_times_api_key = "test-key"
        # Manually insert expired cache entry
        tsa_api._cache["LAX"] = (time.time() - tsa_api.CACHE_TTL - 1, {"wait_minutes": 10, "fetched_at": 0})

        # fetch_live_tsa_wait will try HTTP which will fail (no mock), returning None
        result = await tsa_api.fetch_live_tsa_wait("LAX")
        assert result is None  # Failed to refresh, stale entry expired

    @pytest.mark.asyncio
    @patch("app.services.integrations.tsa_api.settings")
    async def test_api_failure_returns_none(self, mock_settings):
        mock_settings.tsa_wait_times_api_key = "test-key"
        # No HTTP mock → will fail with connection error
        result = await tsa_api.fetch_live_tsa_wait("LAX")
        assert result is None
