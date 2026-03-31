import logging
import uuid as _uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.middleware.auth import get_optional_user, get_required_user
from app.core.errors import UnsupportedModeError
from app.db import get_db
from app.db.models import Trip as TripRow, User
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

    Creates a DRAFT trip. Does NOT increment trip_count.
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
            await db.commit()
        except Exception:
            logger.exception("Failed to link trip %s to user %s", ctx.trip_id, user.id)

    return ctx


@router.post("/{trip_id}/track")
async def track_trip(
    trip_id: str,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """Promote a draft trip to active and increment trip_count."""
    if db is None:
        return {"status": "tracked", "trip_id": trip_id, "trip_count": 0}

    try:
        tid = _uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found")

    row = await db.get(TripRow, tid)
    if row is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    # Ownership check: allow claiming anonymous drafts
    if row.user_id is not None and row.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    if row.user_id is None:
        row.user_id = user.id

    current = row.trip_status or row.status or "draft"

    # Idempotent: already tracked
    if current in ("active", "en_route", "at_airport", "at_gate", "complete"):
        return {"status": "already_tracked", "trip_id": trip_id}

    # Promote draft or created → active
    if current in ("draft", "created"):
        row.status = "active"
        row.trip_status = "active"
        user.trip_count = (user.trip_count or 0) + 1
        await db.commit()
        return {"status": "tracked", "trip_id": trip_id, "trip_count": user.trip_count}

    raise HTTPException(status_code=400, detail=f"Cannot track trip in status {current}")


ACTIVE_STATUSES = ("created", "active", "en_route", "at_airport", "at_gate")


@router.get("/active")
async def get_active_trip(
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    if db is None:
        return {"trip": None}

    today = date.today().isoformat()
    stmt = (
        select(TripRow)
        .where(
            TripRow.user_id == user.id,
            TripRow.status.in_(ACTIVE_STATUSES),
            TripRow.departure_date >= today,
        )
        .order_by(TripRow.departure_date.asc(), TripRow.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return {"trip": None}

    return {
        "trip": {
            "trip_id": str(row.id),
            "flight_number": row.flight_number,
            "departure_date": row.departure_date,
            "home_address": row.home_address,
            "status": row.status,
            "selected_departure_utc": row.selected_departure_utc,
            "preferences_json": row.preferences_json,
        }
    }


@router.get("/{trip_id}")
async def get_trip(
    trip_id: str,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    if db is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    try:
        tid = _uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found")

    row = await db.get(TripRow, tid)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Trip not found")

    return {
        "trip_id": str(row.id),
        "flight_number": row.flight_number,
        "departure_date": row.departure_date,
        "home_address": row.home_address,
        "status": row.status,
        "selected_departure_utc": row.selected_departure_utc,
        "preferences_json": row.preferences_json,
    }
