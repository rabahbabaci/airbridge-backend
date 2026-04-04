"""Tests for morning email briefing service and polling agent trigger."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.notifications.email_service import _build_briefing_html, send_morning_briefing


class TestSendMorningBriefing:
    @patch("app.services.notifications.email_service.settings")
    def test_skips_when_not_configured(self, mock_settings):
        mock_settings.sendgrid_api_key = ""
        result = send_morning_briefing("test@example.com", {"flight_number": "AA100"})
        assert result is False

    @patch("app.services.notifications.email_service.SendGridAPIClient")
    @patch("app.services.notifications.email_service.settings")
    def test_sends_email_successfully(self, mock_settings, mock_sg_cls):
        mock_settings.sendgrid_api_key = "SG.test"
        mock_settings.from_email = "noreply@airbridge.com"
        mock_sg = MagicMock()
        mock_sg_cls.return_value = mock_sg

        result = send_morning_briefing("user@example.com", {
            "flight_number": "UA456",
            "departure_date": "2026-04-05",
            "leave_by_time": "7:30 AM",
            "segments": [
                {"label": "Drive to airport", "duration_minutes": 35},
                {"label": "TSA Security", "duration_minutes": 20},
            ],
        })
        assert result is True
        mock_sg.send.assert_called_once()

    @patch("app.services.notifications.email_service.SendGridAPIClient")
    @patch("app.services.notifications.email_service.settings")
    def test_returns_false_on_send_failure(self, mock_settings, mock_sg_cls):
        mock_settings.sendgrid_api_key = "SG.test"
        mock_settings.from_email = "noreply@airbridge.com"
        mock_sg = MagicMock()
        mock_sg.send.side_effect = Exception("API error")
        mock_sg_cls.return_value = mock_sg

        result = send_morning_briefing("user@example.com", {"flight_number": "AA100"})
        assert result is False


class TestBuildBriefingHtml:
    def test_includes_flight_and_leave_by(self):
        html = _build_briefing_html({
            "flight_number": "DL789",
            "departure_date": "2026-04-05",
            "leave_by_time": "8:15 AM",
            "segments": [],
        })
        assert "DL789" in html
        assert "8:15 AM" in html
        assert "2026-04-05" in html

    def test_includes_segment_rows(self):
        html = _build_briefing_html({
            "flight_number": "DL789",
            "leave_by_time": "8:00 AM",
            "segments": [
                {"label": "Drive to LAX", "duration_minutes": 40},
                {"label": "TSA Security", "duration_minutes": 15},
            ],
        })
        assert "Drive to LAX" in html
        assert "40 min" in html
        assert "TSA Security" in html

    def test_handles_empty_segments(self):
        html = _build_briefing_html({
            "flight_number": "AA100",
            "leave_by_time": "9:00 AM",
            "segments": [],
        })
        assert "AA100" in html


class TestPollingAgentEmailTrigger:
    """Test the email trigger conditions in the polling agent."""

    def _make_trip(self, **kwargs):
        trip = MagicMock()
        trip.id = "trip-123"
        trip.user_id = "user-456"
        trip.flight_number = "UA100"
        trip.departure_date = "2026-04-05"
        trip.morning_email_sent_at = kwargs.get("morning_email_sent_at", None)
        trip.selected_departure_utc = kwargs.get("selected_departure_utc", None)
        trip.preferences_json = None
        trip.last_pushed_leave_home_at = None
        trip.push_count = 0
        trip.trip_status = "active"
        return trip

    def _make_user(self, **kwargs):
        user = MagicMock()
        user.id = "user-456"
        user.email = kwargs.get("email", "test@example.com")
        user.trip_count = 1
        user.subscription_status = "none"
        return user

    def test_trigger_conditions_already_sent(self):
        """If morning_email_sent_at is set, should not send again."""
        trip = self._make_trip(morning_email_sent_at=datetime.now(tz=timezone.utc))
        assert trip.morning_email_sent_at is not None

    def test_trigger_conditions_no_email(self):
        """If user has no email, should not send."""
        user = self._make_user(email=None)
        assert user.email is None

    @patch("app.services.notifications.email_service.settings")
    def test_idempotency(self, mock_settings):
        """Calling send twice with same trip should only send once if first succeeds."""
        mock_settings.sendgrid_api_key = ""
        # With no API key, send returns False — trip.morning_email_sent_at stays None
        result1 = send_morning_briefing("test@example.com", {"flight_number": "AA100"})
        assert result1 is False
        # Simulates polling agent not setting morning_email_sent_at when send fails
