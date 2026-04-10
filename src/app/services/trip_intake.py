import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Union

from app.schemas.trips import (
    FlightNumberTripRequest,
    RouteSearchTripRequest,
    TripContext,
    TripPreferences,
)

logger = logging.getLogger(__name__)

# In-memory store: trip_id (str) -> TripContext. Used by recommendation flow until real persistence exists.
_trip_store: dict[str, TripContext] = {}
_TRIP_STORE_MAX = 1000


def _db_available() -> bool:
    import app.db as _db

    return _db.async_session_factory is not None


async def process_trip_intake(
    payload: Union[FlightNumberTripRequest, RouteSearchTripRequest],
) -> TripContext:
    """
    Validate and normalize a trip intake request.
    Returns a TripContext with a generated trip_id and status='validated'.
    Persists context in DB (if available) and in-memory store.
    """
    now = datetime.now(tz=timezone.utc)
    trip_id = uuid.uuid4()

    if isinstance(payload, FlightNumberTripRequest):
        ctx = TripContext(
            trip_id=trip_id,
            input_mode=payload.input_mode,
            flight_number=payload.flight_number,
            departure_date=payload.departure_date,
            home_address=payload.home_address,
            selected_departure_utc=payload.selected_departure_utc,
            preferences=payload.preferences,
            created_at=now,
        )
    else:
        ctx = TripContext(
            trip_id=trip_id,
            input_mode=payload.input_mode,
            airline=payload.airline,
            origin_airport=payload.origin_airport,
            destination_airport=payload.destination_airport,
            departure_date=payload.departure_date,
            departure_time_window=payload.departure_time_window,
            home_address=payload.home_address,
            preferences=payload.preferences,
            created_at=now,
        )

    # Always write to in-memory store (belt and suspenders)
    if len(_trip_store) >= _TRIP_STORE_MAX:
        oldest_key = next(iter(_trip_store))
        del _trip_store[oldest_key]
    _trip_store[str(trip_id)] = ctx

    # Write to DB if available
    if _db_available():
        try:
            import app.db as _db
            from app.db.models import Trip as TripRow

            async with _db.async_session_factory() as session:
                is_flight_number = isinstance(payload, FlightNumberTripRequest)
                row = TripRow(
                    id=trip_id,
                    input_mode=payload.input_mode,
                    flight_number=(
                        payload.flight_number
                        if is_flight_number
                        else None
                    ),
                    origin_iata=(
                        None if is_flight_number
                        else payload.origin_airport
                    ),
                    destination_iata=(
                        None if is_flight_number
                        else payload.destination_airport
                    ),
                    airline=(
                        None if is_flight_number
                        else payload.airline
                    ),
                    departure_date=str(payload.departure_date),
                    home_address=payload.home_address,
                    selected_departure_utc=(
                        str(payload.selected_departure_utc)
                        if hasattr(payload, "selected_departure_utc")
                        and payload.selected_departure_utc
                        else None
                    ),
                    preferences_json=json.dumps(payload.preferences.model_dump()),
                    status="draft",
                )
                session.add(row)
                await session.commit()
        except Exception:
            logger.exception("Failed to write trip %s to database", trip_id)

    return ctx


async def get_trip_context(trip_id: str) -> TripContext | None:
    """Return stored TripContext for the given trip_id, or None if not found."""
    # Try DB first
    if _db_available():
        try:
            import app.db as _db
            from app.db.models import Trip as TripRow

            async with _db.async_session_factory() as session:
                row = await session.get(TripRow, uuid.UUID(trip_id))
                if row is not None:
                    prefs = TripPreferences()
                    if row.preferences_json:
                        prefs = TripPreferences(**json.loads(row.preferences_json))
                    return TripContext(
                        trip_id=row.id,
                        input_mode=row.input_mode,
                        flight_number=row.flight_number,
                        departure_date=row.departure_date,
                        home_address=row.home_address,
                        selected_departure_utc=row.selected_departure_utc,
                        preferences=prefs,
                        created_at=row.created_at,
                    )
        except Exception:
            logger.exception("Failed to read trip %s from database", trip_id)

    # Fall back to in-memory store
    return _trip_store.get(trip_id)
