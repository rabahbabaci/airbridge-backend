from fastapi import APIRouter

from app.core.errors import UnsupportedModeError
from app.schemas.trips import (
    FlightNumberTripRequest,
    RouteSearchTripRequest,
    TripContext,
    TripRequest,
)
from app.services.trip_intake import process_trip_intake

router = APIRouter(prefix="/trips", tags=["trips"])

_SUPPORTED_MODES = {"flight_number", "route_search"}


@router.post("", response_model=TripContext, status_code=201)
def post_trip(payload: TripRequest) -> TripContext:
    """
    Intake a trip in one of two modes:
    - flight_number: known flight number + departure date + home address
    - route_search: airline + route + time window + home address
    """
    if isinstance(payload, (FlightNumberTripRequest, RouteSearchTripRequest)):
        return process_trip_intake(payload)

    raise UnsupportedModeError(getattr(payload, "input_mode", "unknown"))
