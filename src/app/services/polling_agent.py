"""Background polling agent that monitors active trips and sends push notifications."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from datetime import timedelta

from app.db.models import Event, Trip, User
from app.schemas.recommendations import RecommendationRecomputeRequest
from app.services.notifications import (
    LEAVE_BY_SHIFT,
    POST_TRIP,
    TIME_TO_GO,
    is_pro_user,
    send_trip_notification,
    should_notify_leave_by_shift,
)
from app.services.integrations.airport_defaults import AIRPORT_TIMEZONES
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
        .where(Trip.trip_status.in_(list(MONITORABLE_STATUSES)))
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


def _format_local_time(utc_dt: datetime, airport_iata: str | None) -> str:
    """Format a UTC datetime as local time string using the airport's timezone."""
    if airport_iata and airport_iata in AIRPORT_TIMEZONES:
        local_dt = utc_dt.astimezone(ZoneInfo(AIRPORT_TIMEZONES[airport_iata]))
    else:
        local_dt = utc_dt
    return local_dt.strftime("%I:%M %p").lstrip("0")


INTERACTION_SIGNALS = {"timetogo_tap", "rideshare_tap", "nav_tap"}


def _get_departure_utc(trip_row) -> datetime | None:
    """Parse departure UTC from projected_timeline or selected_departure_utc."""
    timeline = getattr(trip_row, "projected_timeline", None)
    if timeline and isinstance(timeline, dict) and "departure_utc" in timeline:
        try:
            dt = datetime.fromisoformat(timeline["departure_utc"].replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError, AttributeError):
            pass
    # Fallback to selected_departure_utc
    if trip_row.selected_departure_utc:
        try:
            dt = datetime.fromisoformat(
                str(trip_row.selected_departure_utc).replace("Z", "+00:00")
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            pass
    return None


def _get_timeline_dt(trip_row, key: str) -> datetime | None:
    """Parse a datetime from the projected_timeline JSONB."""
    timeline = getattr(trip_row, "projected_timeline", None)
    if not timeline or not isinstance(timeline, dict):
        return None
    val = timeline.get(key)
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


async def _check_interaction_signals(trip_row, user_id, session, since: datetime) -> datetime | None:
    """Check events table for interaction signals since a given time. Returns event timestamp or None."""
    stmt = (
        select(Event)
        .where(
            Event.user_id == user_id,
            Event.event_name.in_(INTERACTION_SIGNALS),
            Event.created_at >= since,
        )
        .order_by(Event.created_at.desc())
        .limit(1)
    )
    event = (await session.execute(stmt)).scalar_one_or_none()
    if event:
        return event.created_at
    return None


async def _advance_trip_state(trip_row, session, now: datetime) -> None:
    """Advance trip state based on time triggers and interaction signals."""
    current = get_trip_status(trip_row)
    user = trip_row.user
    dep_utc = _get_departure_utc(trip_row)

    # Force close: departure + 24h
    if dep_utc and now >= dep_utc + timedelta(hours=24):
        if current != "complete":
            advance_status(trip_row, "complete")
            trip_row.auto_completed = True
            await session.commit()
            logger.info("Trip %s force-closed at departure+24h", trip_row.id)
        return

    if current == "active":
        leave_home_at = _get_timeline_dt(trip_row, "leave_home_at")
        # Time-based: now >= leave_home_at
        if leave_home_at and now >= leave_home_at:
            advance_status(trip_row, "en_route")
            await session.commit()
            logger.info("Trip %s advanced to en_route (time-based)", trip_row.id)
            return
        # Interaction signal: tap within last 10 min
        if user:
            signal_ts = await _check_interaction_signals(
                trip_row, user.id, session, now - timedelta(minutes=10)
            )
            if signal_ts:
                advance_status(trip_row, "en_route")
                trip_row.actual_depart_at = signal_ts
                await session.commit()
                logger.info("Trip %s advanced to en_route (interaction signal)", trip_row.id)
                return

    elif current == "en_route":
        arrive_at = _get_timeline_dt(trip_row, "arrive_airport_at")
        if arrive_at and now >= arrive_at:
            advance_status(trip_row, "at_airport")
            await session.commit()
            logger.info("Trip %s advanced to at_airport", trip_row.id)
            return

    elif current == "at_airport":
        clear_security = _get_timeline_dt(trip_row, "clear_security_at")
        if clear_security and now >= clear_security:
            advance_status(trip_row, "at_gate")
            await session.commit()
            logger.info("Trip %s advanced to at_gate", trip_row.id)
            return

    elif current == "at_gate":
        if dep_utc and now >= dep_utc + timedelta(minutes=30):
            advance_status(trip_row, "complete")
            trip_row.auto_completed = True
            await session.commit()
            logger.info("Trip %s auto-completed at departure+30min", trip_row.id)
            return


async def _handle_feedback_request(trip_row, session, now: datetime) -> None:
    """Send feedback request push after trip completion."""
    if get_trip_status(trip_row) != "complete":
        return
    if not trip_row.user_id:
        return

    dep_utc = _get_departure_utc(trip_row)
    if not dep_utc:
        return

    feedback_requested = getattr(trip_row, "feedback_requested_at", None)

    # First request: departure + 30 min
    if feedback_requested is None and now >= dep_utc + timedelta(minutes=30):
        await send_trip_notification(
            user_id=trip_row.user_id,
            notification_type=POST_TRIP,
            title="How'd it go?",
            body="Tell us about your trip to help improve future predictions",
            trip_row=trip_row,
            session=session,
        )
        trip_row.feedback_requested_at = now
        try:
            await session.commit()
        except Exception:
            logger.exception("Failed to set feedback_requested_at for trip %s", trip_row.id)


async def _process_trip(trip_row, session) -> None:
    """Process a single trip: activate, advance state, recompute, notify."""
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

    # Only monitor monitorable trips
    if get_trip_status(trip_row) not in MONITORABLE_STATUSES:
        return

    # Check pro status
    user = trip_row.user
    if not is_pro_user(user):
        return

    # Advance state based on timeline + interaction signals
    await _advance_trip_state(trip_row, session, now)

    current = get_trip_status(trip_row)

    # Handle feedback request for completed trips
    if current == "complete":
        await _handle_feedback_request(trip_row, session, now)
        return

    # Phase-aware behavior: only recompute + full notify for active phase
    if current != "active":
        # en_route, at_airport, at_gate: minimal polling, no recompute
        return

    # === Active phase: full recompute + notifications ===

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

    # Update projected_timeline from recommendation segments
    if response.segments:
        from app.api.routes.trips import _build_projected_timeline

        dep_utc = _get_departure_utc(trip_row)
        timeline = _build_projected_timeline(
            response, dep_utc.isoformat() if dep_utc else None
        )
        if timeline:
            trip_row.projected_timeline = timeline
        try:
            await session.commit()
        except Exception:
            logger.exception("Failed to update projected_timeline for trip %s", trip_row.id)

    # Morning email: 6 hours before departure, if not already sent
    secs = _seconds_to_departure(trip_row)
    if (
        secs is not None
        and secs <= 6 * 3600
        and getattr(trip_row, "morning_email_sent_at", None) is None
        and user
        and user.email
    ):
        from app.services.notifications.email_service import send_morning_briefing

        segments = [
            {"label": s.label, "duration_minutes": s.duration_minutes}
            for s in response.segments
        ]
        trip_data = {
            "flight_number": trip_row.flight_number,
            "departure_date": trip_row.departure_date,
            "leave_by_time": _format_local_time(new_leave_at, response.origin_airport_code),
            "segments": segments,
        }
        if send_morning_briefing(user.email, trip_data):
            trip_row.morning_email_sent_at = datetime.now(tz=timezone.utc)
            try:
                await session.commit()
            except Exception:
                logger.exception("Failed to update morning_email_sent_at for trip %s", trip_row.id)

    # Check for leave-by shift notification
    if should_notify_leave_by_shift(trip_row.last_pushed_leave_home_at, new_leave_at):
        local_time_str = _format_local_time(new_leave_at, response.origin_airport_code)

        if trip_row.last_pushed_leave_home_at:
            body = f"Your leave-by time changed to {local_time_str}"
            if _get_transport_mode(trip_row) == "rideshare":
                body += " If you booked a ride, you may want to reschedule."
        else:
            body = f"Leave by {local_time_str} to make your flight"
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
        sent = await send_trip_notification(
            user_id=trip_row.user_id,
            notification_type=TIME_TO_GO,
            title="Time to go!",
            body="It's time to leave for your flight",
            trip_row=trip_row,
            session=session,
        )
        if sent and getattr(trip_row, "time_to_go_push_sent_at", None) is None:
            trip_row.time_to_go_push_sent_at = now
            try:
                await session.commit()
            except Exception:
                logger.exception("Failed to update time_to_go_push_sent_at for trip %s", trip_row.id)

    # SMS escalation: 5 min after TIME_TO_GO push, if no user interaction
    time_to_go_sent = getattr(trip_row, "time_to_go_push_sent_at", None)
    sms_count = getattr(trip_row, "sms_count", 0) or 0
    if (
        time_to_go_sent is not None
        and (now - time_to_go_sent).total_seconds() >= 300
        and is_pro_user(user)
        and user
        and user.phone_number
        and sms_count < 3
    ):
        from app.services.notifications.sms_service import send_sms

        tap_stmt = (
            select(Event)
            .where(
                Event.user_id == user.id,
                Event.event_name == "timetogo_tap",
                Event.created_at >= time_to_go_sent,
            )
            .limit(1)
        )
        tap = (await session.execute(tap_stmt)).scalar_one_or_none()

        if tap is None:
            flight = trip_row.flight_number or "your flight"
            if send_sms(
                user.phone_number,
                f"AirBridge: It's time to leave for your {flight} flight!",
            ):
                trip_row.sms_count = sms_count + 1
                try:
                    await session.commit()
                except Exception:
                    logger.exception("Failed to update sms_count for trip %s", trip_row.id)


async def polling_loop() -> None:
    """Infinite loop that polls active trips and processes them."""
    import app.db as _db

    while True:
        if _db.async_session_factory is None:
            await asyncio.sleep(DEFAULT_SLEEP)
            continue

        sleep_interval = DEFAULT_SLEEP
        try:
            async with _db.async_session_factory() as session:
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
