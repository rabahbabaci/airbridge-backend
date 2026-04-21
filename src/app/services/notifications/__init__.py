"""Notification trigger engine for trip push notifications."""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select

from app.db.models import DeviceToken
from app.services.integrations.firebase import send_push, send_push_batch

logger = logging.getLogger(__name__)

# Notification types
LEAVE_BY_SHIFT = "leave_by_shift"
FLIGHT_DELAY = "flight_delay"
GATE_CHANGE = "gate_change"
CANCELLATION = "cancellation"
TIME_TO_GO = "time_to_go"
POST_TRIP = "post_trip"

MAX_PUSHES_PER_TRIP = 5

_INTERRUPTION_LEVELS = {
    LEAVE_BY_SHIFT: "time-sensitive",
    FLIGHT_DELAY: "time-sensitive",
    CANCELLATION: "time-sensitive",
    TIME_TO_GO: "time-sensitive",
    GATE_CHANGE: "active",
    POST_TRIP: "active",
}

_SOUNDS = {
    TIME_TO_GO: "time-to-go.caf",
}


async def get_user_device_tokens(user_id: uuid.UUID, session) -> list[str]:
    """Query DeviceToken table for all tokens belonging to this user."""
    if session is None:
        return []
    try:
        stmt = select(DeviceToken.token).where(DeviceToken.user_id == user_id)
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]
    except Exception:
        logger.exception("Failed to fetch device tokens for user %s", user_id)
        return []


async def send_trip_notification(
    user_id: uuid.UUID,
    notification_type: str,
    title: str,
    body: str,
    trip_row,
    session,
) -> bool:
    """Send a push notification for a trip. Returns True if at least one push succeeded."""
    # Anti-spam check
    push_count = getattr(trip_row, "push_count", 0) or 0
    if push_count >= MAX_PUSHES_PER_TRIP:
        logger.info("Trip %s hit push limit (%d), skipping", trip_row.id, push_count)
        return False

    tokens = await get_user_device_tokens(user_id, session)
    if not tokens:
        logger.debug("No device tokens for user %s", user_id)
        return False

    interruption_level = _INTERRUPTION_LEVELS.get(notification_type, "active")
    sound = _SOUNDS.get(notification_type, "default")
    data = {"trip_id": str(trip_row.id), "type": notification_type}

    if len(tokens) == 1:
        success = send_push(
            token=tokens[0],
            title=title,
            body=body,
            data=data,
            ios_interruption_level=interruption_level,
            sound=sound,
        )
        sent = 1 if success else 0
    else:
        sent = send_push_batch(
            tokens=tokens,
            title=title,
            body=body,
            data=data,
            ios_interruption_level=interruption_level,
            sound=sound,
        )

    if sent > 0:
        trip_row.push_count = push_count + 1
        if session is not None:
            try:
                await session.commit()
            except Exception:
                logger.exception("Failed to update push_count for trip %s", trip_row.id)
        return True

    return False


def should_notify_leave_by_shift(
    old_leave_at: datetime | None, new_leave_at: datetime, threshold_minutes: int = 10
) -> bool:
    """Return True if leave-by time changed significantly or is being set for the first time."""
    if old_leave_at is None:
        return True
    diff = abs((new_leave_at - old_leave_at).total_seconds()) / 60
    return diff >= threshold_minutes


def is_pro_user(user_row) -> bool:
    """Return True if user is on pro tier (subscribed or in trial period)."""
    if user_row is None:
        return False
    if getattr(user_row, "subscription_status", "none") == "active":
        return True
    return getattr(user_row, "trip_count", 0) <= 3
