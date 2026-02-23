import uuid

from app.schemas.trips import TripRequest, TripResponse


def create_trip(payload: TripRequest) -> TripResponse:
    """
    Validate and persist a trip context.
    Placeholder: generates a trip_id and echoes back the input.
    """
    trip_id = str(uuid.uuid4())
    return TripResponse(
        trip_id=trip_id,
        status="created",
        origin_address=payload.origin_address,
        airport_code=payload.airport_code,
        flight_number=payload.flight_number,
        departure_time=payload.departure_time,
        bag_count=payload.bag_count,
        children_count=payload.children_count,
        transport_mode=payload.transport_mode,
    )
