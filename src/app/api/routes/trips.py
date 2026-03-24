import logging

from fastapi import APIRouter, Depends

from app.api.middleware.auth import get_optional_user
from app.core.errors import UnsupportedModeError
from app.db import get_db
from app.db.models import User
from app.schemas.trips import (
    FlightNumberTripRequest,
    RouteSearchTripRequest,
    TripContext,
    TripRequest,
)
from app.services.trip_intake import process_trip_intake

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("", response_model=TripContext, status_code=201)
async def post_trip(
    payload: TripRequest,
    user: User | None = Depends(get_optional_user),
    db=Depends(get_db),
) -> TripContext:
    """
    Intake a trip in one of two modes:
    - flight_number: known flight number + departure date + home address
    - route_search: airline + route + time window + home address
    """
    if not isinstance(payload, (FlightNumberTripRequest, RouteSearchTripRequest)):
        raise UnsupportedModeError(getattr(payload, "input_mode", "unknown"))

    ctx = await process_trip_intake(payload)

    if user is not None and db is not None:
        try:
            from app.db.models import Trip as TripRow
            from sqlalchemy import select

            stmt = select(TripRow).where(TripRow.id == ctx.trip_id)
            trip_row = (await db.execute(stmt)).scalar_one_or_none()
            if trip_row is not None:
                trip_row.user_id = user.id
            user.trip_count = (user.trip_count or 0) + 1
            await db.commit()
        except Exception:
            logger.exception("Failed to link trip %s to user %s", ctx.trip_id, user.id)

    return ctx
