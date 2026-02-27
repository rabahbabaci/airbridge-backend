import uuid
from datetime import datetime, timezone
from typing import Union

from app.schemas.trips import (
    FlightNumberTripRequest,
    RouteSearchTripRequest,
    TripContext,
)

# In-memory store: trip_id (str) -> TripContext. Used by recommendation flow until real persistence exists.
_trip_store: dict[str, TripContext] = {}


def get_trip_context(trip_id: str) -> TripContext | None:
    """Return stored TripContext for the given trip_id, or None if not found."""
    return _trip_store.get(trip_id)


def process_trip_intake(
    payload: Union[FlightNumberTripRequest, RouteSearchTripRequest],
) -> TripContext:
    """
    Validate and normalize a trip intake request.
    Returns a TripContext with a generated trip_id and status='validated'.
    Persists context in memory for recommendation lookup. No external provider calls.
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

    _trip_store[str(trip_id)] = ctx
    return ctx
