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
from app.services.flight_snapshot_service import (
    _select_flight,
    build_flight_info_and_status,
    snapshot_from_columns,
)
from app.services.integrations.aerodatabox import lookup_flights
from app.services.notifications import (
    CANCELLATION,
    GATE_CHANGE,
    LEAVE_BY_SHIFT,
    POST_TRIP,
    TIME_TO_GO,
    is_pro_user,
    send_trip_notification,
    should_notify_leave_by_shift,
)
from app.services.integrations.airport_defaults import AIRPORT_TIMEZONES
from app.services.recommendation_service import (
    build_latest_recommendation_jsonb,
    recompute_recommendation,
)
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
STARTUP_DELAY = 10          # seconds to wait before first poll
BACKOFF_BASE = 60           # initial retry interval on error
BACKOFF_MAX = 900           # max retry interval (15 minutes)
MAX_CONSECUTIVE_ERRORS = 20 # stop polling after this many consecutive failures


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

# Phase B thresholds — skip ADB refresh when the trip is far out, status is
# stable, and last_updated_at is within one polling interval. Final 30 minutes
# force a refresh regardless (handled implicitly by the >6h check failing).
PATH_B_MIN_SECONDS_TO_DEPARTURE = 6 * 3600
PATH_B_STABLE_STATUSES = {"scheduled", "expected"}
# Tracked flight_status fields (excluding last_updated_at / actual_departure_at)
TRACKED_STATUS_FIELDS = ("gate", "status", "delay_minutes", "cancelled")


def _parse_iso_utc(s: str | None) -> datetime | None:
    """Parse an ISO-8601 UTC string into a tz-aware datetime, or None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _should_skip_refresh(trip_row, secs_to_dep: float | None, now: datetime) -> bool:
    """Return True if we can safely skip the ADB refresh this tick (Path B).

    Path B conditions (all must hold):
      * seconds_to_departure > 6 hours
      * flight_status.status is stable ("scheduled"/"expected", case-insensitive)
      * flight_status.cancelled is False
      * flight_status.last_updated_at is within this trip's polling interval
    Otherwise Path A (ADB refresh).
    """
    if secs_to_dep is None or secs_to_dep <= PATH_B_MIN_SECONDS_TO_DEPARTURE:
        return False
    fs = getattr(trip_row, "flight_status", None)
    if not isinstance(fs, dict):
        return False
    if fs.get("cancelled"):
        return False
    status = (fs.get("status") or "").strip().lower()
    if status not in PATH_B_STABLE_STATUSES:
        return False
    last_updated = _parse_iso_utc(fs.get("last_updated_at"))
    if last_updated is None:
        return False
    age = (now - last_updated).total_seconds()
    return age < _get_poll_interval(secs_to_dep)


async def refresh_flight_status(trip_row, session) -> tuple[bool, dict]:
    """Path A: fetch live ADB data and update flight_status on the trip row.

    Returns ``(was_called, changes)`` where ``was_called`` is True iff a live
    ADB response was successfully parsed into a new flight_status. ``changes``
    is ``{field: (old, new)}`` for any tracked field that moved
    (gate / status / delay_minutes / cancelled / terminal).

    flight_info is immutable at the snapshot level; the single documented
    exception is ``terminal``, which may be reassigned by airlines after track.
    When terminal changes, we log at info level and update only that key
    inside flight_info, preserving all other frozen fields.
    """
    flight_number = getattr(trip_row, "flight_number", None)
    departure_date = getattr(trip_row, "departure_date", None)
    if not flight_number or not departure_date:
        return (False, {})

    try:
        flights = lookup_flights(flight_number, str(departure_date))
    except Exception:
        logger.exception(
            "refresh_flight_status: lookup_flights raised for trip %s", trip_row.id
        )
        return (False, {})

    if not flights:
        return (False, {})

    selected = _select_flight(flights, getattr(trip_row, "selected_departure_utc", None))
    new_info, new_status = build_flight_info_and_status(selected)
    if not new_status:
        return (False, {})

    old_status = getattr(trip_row, "flight_status", None) or {}
    changes: dict = {}
    for field in TRACKED_STATUS_FIELDS:
        old_v = old_status.get(field)
        new_v = new_status.get(field)
        if old_v != new_v:
            changes[field] = (old_v, new_v)

    old_info = getattr(trip_row, "flight_info", None) or {}
    old_terminal = old_info.get("terminal")
    new_terminal = (new_info or {}).get("terminal")
    if new_terminal is not None and new_terminal != old_terminal:
        changes["terminal"] = (old_terminal, new_terminal)
        logger.info(
            "Trip %s terminal changed: %s -> %s",
            trip_row.id, old_terminal, new_terminal,
        )
        if trip_row.flight_info is not None:
            updated_info = dict(trip_row.flight_info)
            updated_info["terminal"] = new_terminal
            trip_row.flight_info = updated_info

    # Replace the whole flight_status dict — ADB is the source of truth for
    # every live field and assigning a new object triggers SQLAlchemy dirty
    # tracking for the JSON column (in-place mutation would not).
    trip_row.flight_status = new_status
    return (True, changes)


async def _handle_status_change_notifications(
    trip_row, session, changes: dict
) -> None:
    """Fire pushes for detected flight status changes from refresh_flight_status."""
    if not changes or not getattr(trip_row, "user_id", None):
        return

    flight_label = trip_row.flight_number or "your flight"

    if "cancelled" in changes:
        old, new = changes["cancelled"]
        if new and not old:
            await send_trip_notification(
                user_id=trip_row.user_id,
                notification_type=CANCELLATION,
                title="Flight cancelled",
                body=f"{flight_label} has been cancelled. Check with your airline for rebooking.",
                trip_row=trip_row,
                session=session,
            )

    if "gate" in changes:
        old, new = changes["gate"]
        if new and new != old:
            body = f"{flight_label}: gate changed to {new}"
            if old:
                body += f" (from {old})"
            await send_trip_notification(
                user_id=trip_row.user_id,
                notification_type=GATE_CHANGE,
                title="Gate changed",
                body=body,
                trip_row=trip_row,
                session=session,
            )


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

    # Phase 2: Path A/B status refresh. Runs for every monitorable status
    # (active, en_route, at_airport, at_gate) — en_route+ phases still get
    # fresh gate/cancellation info, just no recompute afterward.
    secs_to_dep = _seconds_to_departure(trip_row)
    changes: dict = {}
    if (
        getattr(trip_row, "input_mode", None) == "flight_number"
        and getattr(trip_row, "flight_number", None)
        and not _should_skip_refresh(trip_row, secs_to_dep, now)
    ):
        try:
            was_called, changes = await refresh_flight_status(trip_row, session)
            if was_called:
                try:
                    await session.commit()
                except Exception:
                    logger.exception(
                        "Failed to commit flight_status for trip %s", trip_row.id
                    )
        except Exception:
            logger.exception("refresh_flight_status failed for trip %s", trip_row.id)

    # Fire push notifications for detected status changes (any monitorable phase)
    if changes:
        try:
            await _handle_status_change_notifications(trip_row, session, changes)
        except Exception:
            logger.exception(
                "Failed to dispatch status-change notifications for trip %s",
                trip_row.id,
            )

    # Advance state based on timeline + interaction signals
    await _advance_trip_state(trip_row, session, now)

    current = get_trip_status(trip_row)

    # Handle feedback request for completed trips
    if current == "complete":
        await _handle_feedback_request(trip_row, session, now)
        return

    # Phase-aware behavior: only recompute + full notify for active phase.
    # en_route / at_airport / at_gate already had their status refreshed and
    # any gate-change/cancellation pushes fired above.
    if current != "active":
        return

    # === Active phase: full recompute + notifications ===

    # Recompute recommendation. Pass a FlightSnapshot reconstructed from the
    # persisted columns when possible, so we don't re-hit ADB here.
    prefetched_snapshot = None
    flight_info = getattr(trip_row, "flight_info", None)
    if flight_info:
        prefetched_snapshot = snapshot_from_columns(
            flight_info, getattr(trip_row, "flight_status", None)
        )
        if prefetched_snapshot is None:
            logger.warning(
                "Trip %s has flight_info but snapshot_from_columns returned None; "
                "falling back to fresh ADB path",
                trip_row.id,
            )

    try:
        payload = RecommendationRecomputeRequest(trip_id=str(trip_row.id))
        response = await recompute_recommendation(
            payload, user=user, prefetched_snapshot=prefetched_snapshot
        )
        if response is None:
            return
    except Exception:
        logger.exception("Failed to recompute recommendation for trip %s", trip_row.id)
        return

    new_leave_at = response.leave_home_at

    # Persist latest_recommendation so the Active Trip Screen can render segments +
    # map coordinates from the trip row (no round-trip to /v1/recommendations).
    trip_row.latest_recommendation = build_latest_recommendation_jsonb(response)

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
        logger.exception(
            "Failed to commit projected_timeline / latest_recommendation for trip %s",
            trip_row.id,
        )

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


def compute_backoff(consecutive_errors: int) -> int:
    """Compute backoff interval: 60s, 120s, 240s, ..., capped at 900s."""
    return min(BACKOFF_BASE * (2 ** (consecutive_errors - 1)), BACKOFF_MAX)


def compute_backoff(consecutive_errors: int) -> int:
    """Compute backoff interval: 60s, 120s, 240s, ..., capped at 900s."""
    return min(BACKOFF_BASE * (2 ** (consecutive_errors - 1)), BACKOFF_MAX)


async def polling_loop() -> None:
    """Infinite loop that polls active trips and processes them."""
    import app.db as _db

    # Startup delay — let the container and pooler stabilize
    await asyncio.sleep(STARTUP_DELAY)

    consecutive_errors = 0

    # Startup delay — let the container and pooler stabilize
    await asyncio.sleep(STARTUP_DELAY)

    consecutive_errors = 0

    while True:
        if _db.async_session_factory is None:
            await asyncio.sleep(DEFAULT_SLEEP)
            continue

        sleep_interval = DEFAULT_SLEEP
        try:
            async with _db.async_session_factory() as session:
                trips = await _get_active_trips(session)

                if not trips:
                    consecutive_errors = 0  # successful DB query
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

            # Success — reset backoff
            consecutive_errors = 0

        except Exception:
            consecutive_errors += 1
            backoff = compute_backoff(consecutive_errors)
            logger.exception(
                "Polling loop error (consecutive_errors=%d, next_retry=%ds)",
                consecutive_errors, backoff,
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    "Polling agent stopped after %d consecutive errors "
                    "(approximately 4 hours of failures). "
                    "Manual restart required via redeploy.",
                    consecutive_errors,
                )
                return  # exit the loop entirely

            await asyncio.sleep(backoff)
            continue  # skip the normal sleep below

        await asyncio.sleep(sleep_interval)


async def start_polling_agent() -> None:
    """Start the polling agent as an asyncio task."""
    logger.info("Polling agent started")
    await polling_loop()
