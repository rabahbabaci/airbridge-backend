"""Trip status state machine: created → active → en_route → at_airport → at_gate → complete."""

from datetime import datetime, timezone

STATUS_ORDER = ["created", "active", "en_route", "at_airport", "at_gate", "complete"]
MONITORABLE_STATUSES = {"active", "en_route"}


def get_trip_status(trip_row) -> str:
    """Return current status from the DB row."""
    return trip_row.trip_status or trip_row.status or "created"


def should_activate(trip_row, now: datetime) -> bool:
    """Return True if trip is 'created' and departure is within 24 hours."""
    if get_trip_status(trip_row) != "created":
        return False

    dep_time = _parse_departure_time(trip_row)
    if dep_time is None:
        return False

    hours_until = (dep_time - now).total_seconds() / 3600
    return 0 < hours_until <= 24


def advance_status(trip_row, new_status: str) -> None:
    """Advance trip to new_status. Only forward transitions allowed."""
    current = get_trip_status(trip_row)
    if current not in STATUS_ORDER or new_status not in STATUS_ORDER:
        raise ValueError(f"Unknown status: {current!r} or {new_status!r}")

    current_idx = STATUS_ORDER.index(current)
    new_idx = STATUS_ORDER.index(new_status)

    if new_idx <= current_idx:
        raise ValueError(
            f"Cannot transition from {current!r} to {new_status!r} (only forward transitions allowed)"
        )

    trip_row.status = new_status
    trip_row.trip_status = new_status


def _parse_departure_time(trip_row) -> datetime | None:
    """Parse departure time from trip row. Uses selected_departure_utc if available, else noon UTC on departure_date."""
    if trip_row.selected_departure_utc:
        try:
            dt = datetime.fromisoformat(str(trip_row.selected_departure_utc).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            pass

    if trip_row.departure_date:
        try:
            date_str = str(trip_row.departure_date).strip()[:10]
            return datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    return None
