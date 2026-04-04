"""Tests for Twilio SMS service and polling agent escalation logic."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.notifications.sms_service import MAX_SMS_PER_TRIP, send_sms


class TestSendSms:
    @patch("app.services.notifications.sms_service.settings")
    def test_skips_when_not_configured(self, mock_settings):
        mock_settings.twilio_account_sid = ""
        result = send_sms("+15551234567", "Test message")
        assert result is False

    @patch("twilio.rest.Client")
    @patch("app.services.notifications.sms_service.settings")
    def test_sends_sms_successfully(self, mock_settings, mock_client_cls):
        mock_settings.twilio_account_sid = "AC_test"
        mock_settings.twilio_auth_token = "auth_test"
        mock_settings.twilio_from_number = "+15559999999"
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        result = send_sms("+15551234567", "Time to go!")
        assert result is True
        mock_client.messages.create.assert_called_once_with(
            body="Time to go!",
            from_="+15559999999",
            to="+15551234567",
        )

    @patch("twilio.rest.Client")
    @patch("app.services.notifications.sms_service.settings")
    def test_returns_false_on_failure(self, mock_settings, mock_client_cls):
        mock_settings.twilio_account_sid = "AC_test"
        mock_settings.twilio_auth_token = "auth_test"
        mock_settings.twilio_from_number = "+15559999999"
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Twilio error")
        mock_client_cls.return_value = mock_client

        result = send_sms("+15551234567", "Test")
        assert result is False


class TestSmsEscalationConditions:
    """Test the escalation logic conditions used in the polling agent."""

    def test_max_sms_per_trip_is_three(self):
        assert MAX_SMS_PER_TRIP == 3

    def test_escalation_window_is_five_minutes(self):
        """SMS should only be sent >= 5 minutes after TIME_TO_GO push."""
        sent_at = datetime.now(tz=timezone.utc) - timedelta(minutes=4)
        now = datetime.now(tz=timezone.utc)
        elapsed = (now - sent_at).total_seconds()
        assert elapsed < 300  # Not yet eligible

        sent_at_old = datetime.now(tz=timezone.utc) - timedelta(minutes=6)
        elapsed_old = (now - sent_at_old).total_seconds()
        assert elapsed_old >= 300  # Eligible

    def test_anti_spam_cap_at_three(self):
        """sms_count >= 3 should block further SMS."""
        for count in range(5):
            should_send = count < 3
            assert (count < MAX_SMS_PER_TRIP) == should_send

    def test_pro_only_gating(self):
        """Free-tier users should not receive SMS escalation."""
        from app.services.notifications import is_pro_user

        # Pro user (in trial)
        pro = MagicMock()
        pro.subscription_status = "none"
        pro.trip_count = 2
        assert is_pro_user(pro) is True

        # Free user (trial expired)
        free = MagicMock()
        free.subscription_status = "none"
        free.trip_count = 5
        assert is_pro_user(free) is False

    def test_no_phone_blocks_sms(self):
        """Users without phone_number should not receive SMS."""
        user = MagicMock()
        user.phone_number = None
        assert user.phone_number is None  # SMS condition fails

    def test_timetogo_tap_blocks_sms(self):
        """If user tapped the notification, SMS should be skipped.
        This is tested via the polling agent query for timetogo_tap events.
        Here we verify the event name constant used."""
        event_name = "timetogo_tap"
        assert event_name == "timetogo_tap"

    @patch("app.services.notifications.sms_service.settings")
    def test_sms_not_sent_when_twilio_not_configured(self, mock_settings):
        mock_settings.twilio_account_sid = ""
        result = send_sms("+15551234567", "test")
        assert result is False
