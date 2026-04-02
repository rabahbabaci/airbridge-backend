"""Background polling agent that monitors active trips and sends push notifications."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Trip, User
from app.schemas.recommendations import RecommendationRecomputeRequest
from app.services.notifications import (
    LEAVE_BY_SHIFT,
    TIME_TO_GO,
    is_pro_user,
    send_trip_notification,
    should_notify_leave_by_shift,
)
from app.services.recommendation_service import recompute_recommendation
from app.services.trip_state import (
    MONITORABLE_STATUSES,
    advance_status,
    get_trip_status,
    should_activate,
)

logger = logging.getLogger(__name__)

# Poll intervals based on time-to-departure
POLL_INTERVALS = [
    (6 * 3600, 1800),   # > 6 hours: every 30 min
    (2 * 3600, 600),    # 2-6 hours: every 10 min
    (0, 300),           # < 2 hours: every 5 min
]

DEFAULT_SLEEP = 60


async def _get_active_trips(session) -> list:
    """Query trips with monitorable statuses, with user relationship loaded."""
    stmt = (
        select(Trip)
        .where(Trip.trip_status.in_(["active", "en_route"]))
        .options(selectinload(Trip.user))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


def _seconds_to_departure(trip_row) -> float | None:
    """Return seconds until departure, or None if unparseable."""
    if trip_row.selected_departure_utc:
        try:
            dt = datetime.fromisoformat(
                str(trip_row.selected_departure_utc).replace("Z", "+00:00")
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt - datetime.now(tz=timezone.utc)).total_seconds()
        except (ValueError, TypeError):
            pass

    if trip_row.departure_date:
        try:
            date_str = str(trip_row.departure_date).strip()[:10]
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
            return (dt - datetime.now(tz=timezone.utc)).total_seconds()
        except (ValueError, TypeError):
            pass

    return None


def _get_poll_interval(seconds_to_dep: float | None) -> int:
    """Return appropriate poll interval in seconds based on time to departure."""
    if seconds_to_dep is None:
        return DEFAULT_SLEEP
    for threshold, interval in POLL_INTERVALS:
        if seconds_to_dep > threshold:
            return interval
    return POLL_INTERVALS[-1][1]


def _get_transport_mode(trip_row) -> str | None:
    """Safely extract transport_mode from trip preferences_json."""
    raw = getattr(trip_row, "preferences_json", None)
    if not raw:
        return None
    try:
        prefs = json.loads(raw)
        return prefs.get("transport_mode") if isinstance(prefs, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


async def _process_trip(trip_row, session) -> None:
    """Process a single trip: activate, recompute, notify."""
    now = datetime.now(tz=timezone.utc)

    # Activate if within 24 hours
    if should_activate(trip_row, now) and get_trip_status(trip_row) == "created":
        try:
            advance_status(trip_row, "active")
            await session.commit()
            logger.info("Trip %s activated", trip_row.id)
        except Exception:
            logger.exception("Failed to activate trip %s", trip_row.id)
            return

    # Only monitor active/en_route trips
    if get_trip_status(trip_row) not in MONITORABLE_STATUSES:
        return

    # Check pro status
    user = trip_row.user
    if not is_pro_user(user):
        return

    # Recompute recommendation
    try:
        payload = RecommendationRecomputeRequest(trip_id=str(trip_row.id))
        response = await recompute_recommendation(payload, user=user)
        if response is None:
            return
    except Exception:
        logger.exception("Failed to recompute recommendation for trip %s", trip_row.id)
        return

    new_leave_at = response.leave_home_at

    # Check for leave-by shift notification
    if should_notify_leave_by_shift(trip_row.last_pushed_leave_home_at, new_leave_at):
        if trip_row.last_pushed_leave_home_at:
            body = f"Your leave-by time changed to {new_leave_at.strftime('%I:%M %p')}"
            if _get_transport_mode(trip_row) == "rideshare":
                body += " If you booked a ride, you may want to reschedule."
        else:
            body = f"Leave by {new_leave_at.strftime('%I:%M %p')} to make your flight"
            if _get_transport_mode(trip_row) == "rideshare":
                body += " If you booked a ride, schedule your pickup accordingly."

        await send_trip_notification(
            user_id=trip_row.user_id,
            notification_type=LEAVE_BY_SHIFT,
            title="Leave-by time changed",
            body=body,
            trip_row=trip_row,
            session=session,
        )
        trip_row.last_pushed_leave_home_at = new_leave_at
        try:
            await session.commit()
        except Exception:
            logger.exception("Failed to update last_pushed_leave_home_at for trip %s", trip_row.id)

    # Time-to-go nudge: if now >= leave_home_at and we haven't sent one
    if now >= new_leave_at and (
        trip_row.last_pushed_leave_home_at is None
        or trip_row.last_pushed_leave_home_at < new_leave_at
        or get_trip_status(trip_row) == "active"
    ):
        await send_trip_notification(
            user_id=trip_row.user_id,
            notification_type=TIME_TO_GO,
            title="Time to go!",
            body="It's time to leave for your flight",
            trip_row=trip_row,
            session=session,
        )


async def polling_loop() -> None:
    """Infinite loop that polls active trips and processes them."""
    from app.db import async_session_factory

    while True:
        if async_session_factory is None:
            await asyncio.sleep(DEFAULT_SLEEP)
            continue

        sleep_interval = DEFAULT_SLEEP
        try:
            async with async_session_factory() as session:
                trips = await _get_active_trips(session)

                if not trips:
                    await asyncio.sleep(DEFAULT_SLEEP)
                    continue

                # Determine shortest needed interval
                min_interval = DEFAULT_SLEEP
                for trip in trips:
                    secs = _seconds_to_departure(trip)
                    interval = _get_poll_interval(secs)
                    min_interval = min(min_interval, interval)

                sleep_interval = min_interval

                for trip in trips:
                    try:
                        await _process_trip(trip, session)
                    except Exception:
                        logger.exception("Error processing trip %s", trip.id)

        except Exception:
            logger.exception("Polling loop error")

        await asyncio.sleep(sleep_interval)


async def start_polling_agent() -> None:
    """Start the polling agent as an asyncio task."""
    logger.info("Polling agent started")
    await polling_loop()
