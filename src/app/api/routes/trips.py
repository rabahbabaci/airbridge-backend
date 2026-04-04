import logging
import uuid as _uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from pydantic import BaseModel

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


class UpdateTripRequest(BaseModel):
    home_address: str | None = None
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

        # Compute initial projected_timeline
        try:
            from app.schemas.recommendations import RecommendationRequest
            from app.services.recommendation_service import compute_recommendation

            rec_response = await compute_recommendation(
                RecommendationRequest(trip_id=trip_id), user=user
            )
            if rec_response and rec_response.segments:
                from datetime import timedelta
                cursor = rec_response.leave_home_at
                arrive_airport_at = None
                clear_security_at = None
                at_gate_at = None
                for seg in rec_response.segments:
                    seg_end = cursor + timedelta(minutes=seg.duration_minutes)
                    seg_id = seg.id.lower() if seg.id else ""
                    if any(k in seg_id for k in ("transport", "drive", "parking", "transit")):
                        arrive_airport_at = seg_end
                    elif "tsa" in seg_id or "security" in seg_id:
                        clear_security_at = seg_end
                    elif "gate" in seg_id:
                        at_gate_at = seg_end
                    cursor = seg_end

                dep_utc = row.selected_departure_utc
                row.projected_timeline = {
                    "leave_home_at": rec_response.leave_home_at.isoformat(),
                    "arrive_airport_at": arrive_airport_at.isoformat() if arrive_airport_at else None,
                    "clear_security_at": clear_security_at.isoformat() if clear_security_at else None,
                    "at_gate_at": at_gate_at.isoformat() if at_gate_at else None,
                    "departure_utc": dep_utc,
                    "computed_at": date.today().isoformat(),
                }
        except Exception:
            logger.warning("Failed to compute projected_timeline on track for trip %s", trip_id)

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


@router.get("/history")
async def get_trip_history(
    limit: int = 10,
    offset: int = 0,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """Return completed trips with optional feedback data."""
    if db is None:
        return {"trips": [], "total": 0, "avg_accuracy_minutes": None, "total_trips_with_feedback": 0}

    from app.services.trial import is_pro
    from app.db.models import Feedback
    from sqlalchemy import func
    from sqlalchemy.orm import selectinload

    # Pro gating: free tier max 5 trips total
    max_results = limit if is_pro(user) else min(limit, 5)

    # Count total completed trips
    count_stmt = (
        select(func.count(TripRow.id))
        .where(TripRow.user_id == user.id, TripRow.trip_status == "complete")
    )
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch trips with feedback
    stmt = (
        select(TripRow)
        .where(TripRow.user_id == user.id, TripRow.trip_status == "complete")
        .options(selectinload(TripRow.feedbacks))
        .order_by(TripRow.created_at.desc())
        .offset(offset)
        .limit(max_results)
    )
    rows = (await db.execute(stmt)).scalars().all()

    trips = []
    for row in rows:
        fb = row.feedbacks[0] if row.feedbacks else None
        trips.append({
            "trip_id": str(row.id),
            "flight_number": row.flight_number,
            "departure_date": row.departure_date,
            "home_address": row.home_address,
            "status": row.trip_status,
            "feedback": {
                "followed_recommendation": fb.followed_recommendation,
                "minutes_at_gate": fb.minutes_at_gate,
                "actual_tsa_wait_minutes": fb.actual_tsa_wait_minutes,
            } if fb else None,
        })

    # Aggregate accuracy stats
    agg_stmt = select(
        func.count(Feedback.id),
        func.avg(Feedback.minutes_at_gate),
    ).where(
        Feedback.user_id == user.id,
        Feedback.minutes_at_gate.is_not(None),
    )
    agg = (await db.execute(agg_stmt)).one()
    total_with_feedback = agg[0] or 0
    avg_gate = agg[1]
    avg_accuracy = round(abs(float(avg_gate) - 30), 1) if avg_gate is not None else None

    return {
        "trips": trips,
        "total": total,
        "avg_accuracy_minutes": avg_accuracy,
        "total_trips_with_feedback": total_with_feedback,
    }


@router.put("/{trip_id}")
async def update_trip(
    trip_id: str,
    payload: UpdateTripRequest,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """Update mutable fields on a trip."""
    if db is None:
        return {"status": "updated", "trip_id": trip_id}

    try:
        tid = _uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found")

    row = await db.get(TripRow, tid)
    if row is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    if row.user_id is not None and row.user_id != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if payload.home_address is not None:
        row.home_address = payload.home_address

    await db.commit()
    return {"status": "updated", "trip_id": trip_id}


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
