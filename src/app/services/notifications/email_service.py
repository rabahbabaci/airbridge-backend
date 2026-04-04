"""SendGrid email service for morning-of departure briefings."""

import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_briefing_html(trip_data: dict) -> str:
    """Build HTML email body for the morning briefing."""
    flight = trip_data.get("flight_number", "your flight")
    leave_by = trip_data.get("leave_by_time", "—")
    departure_date = trip_data.get("departure_date", "")
    segments = trip_data.get("segments", [])

    segment_rows = ""
    for seg in segments:
        label = seg.get("label", "")
        duration = seg.get("duration_minutes", 0)
        segment_rows += f"<tr><td style='padding:4px 8px'>{label}</td><td style='padding:4px 8px'>{duration} min</td></tr>"

    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 480px; margin: 0 auto;">
        <h2 style="color: #1a1a2e;">Your AirBridge Briefing</h2>
        <p><strong>Flight:</strong> {flight} &middot; {departure_date}</p>
        <p style="font-size: 20px; color: #0f3460;">Leave by <strong>{leave_by}</strong></p>
        {f'''<table style="width:100%; border-collapse:collapse; margin-top:12px;">
            <thead><tr style="border-bottom:1px solid #ddd;">
                <th style="text-align:left; padding:4px 8px;">Segment</th>
                <th style="text-align:left; padding:4px 8px;">Time</th>
            </tr></thead>
            <tbody>{segment_rows}</tbody>
        </table>''' if segment_rows else ''}
        <p style="margin-top: 20px; font-size: 13px; color: #888;">
            Open AirBridge for real-time updates.
        </p>
    </div>
    """


def send_morning_briefing(to_email: str, trip_data: dict) -> bool:
    """Send morning-of email briefing. Returns True on success."""
    if not settings.sendgrid_api_key:
        logger.debug("SendGrid not configured, skipping email")
        return False

    flight = trip_data.get("flight_number", "your flight")
    html = _build_briefing_html(trip_data)

    message = Mail(
        from_email=settings.from_email,
        to_emails=to_email,
        subject=f"Your AirBridge briefing for {flight}",
        html_content=html,
    )

    try:
        sg = SendGridAPIClient(settings.sendgrid_api_key)
        sg.send(message)
        logger.info("Morning briefing sent to %s for %s", to_email, flight)
        return True
    except Exception:
        logger.exception("Failed to send morning briefing to %s", to_email)
        return False
