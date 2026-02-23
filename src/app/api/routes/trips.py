from fastapi import APIRouter

from app.schemas.trips import TripRequest, TripResponse
from app.services.trip_service import create_trip

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("", response_model=TripResponse, status_code=201)
def post_trip(payload: TripRequest) -> TripResponse:
    """Create and validate a trip context."""
    return create_trip(payload)
