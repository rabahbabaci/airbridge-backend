"""Twilio SMS service for time-to-go escalation."""

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_SMS_PER_TRIP = 3


def send_sms(to_number: str, body: str) -> bool:
    """Send an SMS via Twilio. Returns True on success."""
    if not settings.twilio_account_sid:
        logger.debug("Twilio not configured, skipping SMS")
        return False

    try:
        from twilio.rest import Client

        client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        client.messages.create(
            body=body,
            from_=settings.twilio_from_number,
            to=to_number,
        )
        logger.info("SMS sent to %s", to_number[:6] + "****")
        return True
    except Exception:
        logger.exception("Failed to send SMS to %s", to_number[:6] + "****")
        return False
