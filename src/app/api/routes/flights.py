from fastapi import APIRouter, HTTPException

from app.services.flight_snapshot_service import get_available_flights

router = APIRouter(prefix="/flights", tags=["flights"])


@router.get("/{flight_number}/{date}")
def get_flights(flight_number: str, date: str):
    """Return available flights for the given flight number and date."""
    result = get_available_flights(flight_number, date)
    if not result:
        raise HTTPException(
            status_code=404,
            detail="No flights found for this flight number and date",
        )
    return {"flights": result}
