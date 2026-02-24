import uuid
from datetime import datetime, timezone
from typing import Union

from app.schemas.trips import (
    FlightNumberTripRequest,
    RouteSearchTripRequest,
    TripContext,
)


def process_trip_intake(
    payload: Union[FlightNumberTripRequest, RouteSearchTripRequest],
) -> TripContext:
    """
    Validate and normalize a trip intake request.
    Returns a TripContext with a generated trip_id and status='validated'.
    No external provider calls are made at this stage.
    """
    now = datetime.now(tz=timezone.utc)
    trip_id = uuid.uuid4()

    if isinstance(payload, FlightNumberTripRequest):
        return TripContext(
            trip_id=trip_id,
            input_mode=payload.input_mode,
            flight_number=payload.flight_number,
            departure_date=payload.departure_date,
            home_address=payload.home_address,
            created_at=now,
        )

    return TripContext(
        trip_id=trip_id,
        input_mode=payload.input_mode,
        airline=payload.airline,
        origin_airport=payload.origin_airport,
        destination_airport=payload.destination_airport,
        departure_date=payload.departure_date,
        departure_time_window=payload.departure_time_window,
        home_address=payload.home_address,
        created_at=now,
    )
