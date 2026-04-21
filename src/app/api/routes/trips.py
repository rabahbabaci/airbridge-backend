import logging
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from pydantic import BaseModel, Field, field_validator

from app.api.middleware.auth import get_optional_user, get_required_user
from app.core.errors import UnsupportedModeError
from app.db import get_db
from app.db.models import Trip as TripRow, User
from app.schemas.trips import (
    EXTRA_TIME_MINUTES_VALUES,
    ConfidenceProfile,
    FlightNumberTripRequest,
    RouteSearchTripRequest,
    TripContext,
    TripRequest,
)
from app.services.trip_intake import process_trip_intake


class FlightInfo(BaseModel):
    airline: str | None = None
    flight_number: str | None = None
    origin_iata: str | None = None
    destination_iata: str | None = None
    scheduled_departure_at: str | None = None
    scheduled_arrival_at: str | None = None
    aircraft_type: str | None = None
    terminal: str | None = None
    duration_minutes: int | None = None
    snapshot_taken_at: str | None = None


class FlightStatus(BaseModel):
    gate: str | None = None
    status: str | None = None
    delay_minutes: int = 0
    actual_departure_at: str | None = None
    cancelled: bool = False
    last_updated_at: str | None = None


class TrackTripResponse(BaseModel):
    status: str
    trip_id: str
    trip_count: int = 0
    flight_info: FlightInfo | None = None
    flight_status: FlightStatus | None = None


class TripDetailResponse(BaseModel):
    """Shape returned by GET /v1/trips/{id}. Carries everything the Active Trip
    Screen needs so it can render from one round-trip (Phase 3 Option B)."""

    trip_id: str
    flight_number: str | None = None
    departure_date: str | None = None
    home_address: str | None = None
    status: str
    selected_departure_utc: str | None = None
    preferences_json: str | None = None
    origin_iata: str | None = None
    destination_iata: str | None = None
    airline: str | None = None
    projected_timeline: dict | None = None
    flight_info: dict | None = None
    flight_status: dict | None = None
    latest_recommendation: dict | None = None


class UpdateTripRequest(BaseModel):
    home_address: str | None = Field(None, max_length=500)
    flight_number: str | None = Field(None, max_length=10)
    departure_date: str | None = Field(None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    transport_mode: str | None = Field(None, max_length=20)
    security_access: str | None = Field(None, max_length=20)
    buffer_preference: int | None = Field(None, ge=0, le=180)
    bag_count: int | None = Field(None, ge=0, le=10)
    traveling_with_children: bool | None = None
    has_boarding_pass: bool | None = None
    extra_time_minutes: int | None = None
    confidence_profile: ConfidenceProfile | None = None

    @field_validator("flight_number", mode="before")
    @classmethod
    def normalize_flight_number(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().upper()

    @field_validator("extra_time_minutes", mode="before")
    @classmethod
    def extra_time_values(cls, v: object) -> int | None:
        if v is None:
            return None
        if isinstance(v, int) and v in EXTRA_TIME_MINUTES_VALUES:
            return v
        if isinstance(v, str) and v.strip().isdigit():
            n = int(v.strip())
            if n in EXTRA_TIME_MINUTES_VALUES:
                return n
        raise ValueError("extra_time_minutes must be 0, 15, or 30")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trips", tags=["trips"])


def _build_projected_timeline(rec_response, dep_utc: str | None) -> dict | None:
    """Build projected_timeline dict from recommendation response segments.

    Used by track_trip, update_trip, and polling_agent.
    """
    if not rec_response or not rec_response.segments:
        return None

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

    return {
        "leave_home_at": rec_response.leave_home_at.isoformat(),
        "arrive_airport_at": arrive_airport_at.isoformat() if arrive_airport_at else None,
        "clear_security_at": clear_security_at.isoformat() if clear_security_at else None,
        "at_gate_at": at_gate_at.isoformat() if at_gate_at else None,
        "departure_utc": dep_utc,
        "computed_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _compute_accuracy_delta(row, feedback) -> int | None:
    """Compute accuracy_delta = actual_gate_wait - predicted_gate_buffer.

    Positive = arrived earlier than predicted (more gate time than expected).
    Negative = arrived later than predicted (less gate time than expected).
    Returns None when data is missing.
    """
    if feedback is None or feedback.minutes_at_gate is None:
        return None
    timeline = row.projected_timeline
    if not timeline:
        return None
    at_gate_at = timeline.get("at_gate_at")
    departure_utc = timeline.get("departure_utc")
    if not at_gate_at or not departure_utc:
        return None
    try:
        gate_dt = datetime.fromisoformat(str(at_gate_at).replace("Z", "+00:00"))
        dep_dt = datetime.fromisoformat(str(departure_utc).replace("Z", "+00:00"))
        predicted_gate_buffer = (dep_dt - gate_dt).total_seconds() / 60
        return round(feedback.minutes_at_gate - predicted_gate_buffer)
    except (ValueError, TypeError):
        return None


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


@router.post("/{trip_id}/track", response_model=TrackTripResponse)
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

    current = row.trip_status

    # Idempotent: already tracked
    if current in ("active", "en_route", "at_airport", "at_gate", "complete"):
        return {"status": "already_tracked", "trip_id": trip_id}

    # Promote draft or created → active
    if current in ("draft", "created"):
        row.status = "active"
        row.trip_status = "active"
        user.trip_count = (user.trip_count or 0) + 1

        # Compute initial projected_timeline. This also warms the flight_snapshot_service
        # cache with the raw AeroDataBox response, which get_selected_flight reuses below.
        try:
            from app.schemas.recommendations import RecommendationRequest
            from app.services.recommendation_service import (
                build_latest_recommendation_jsonb,
                compute_recommendation,
            )

            rec_response = await compute_recommendation(
                RecommendationRequest(trip_id=trip_id), user=user
            )
            timeline = _build_projected_timeline(rec_response, row.selected_departure_utc)
            if timeline:
                row.projected_timeline = timeline
            if rec_response is not None:
                row.latest_recommendation = build_latest_recommendation_jsonb(rec_response)
        except Exception:
            logger.exception("Failed to compute projected_timeline on track for trip %s", trip_id)

        # Persist frozen flight_info + initial flight_status from the cached ADB response.
        # flight_info is the source of truth on conflict; origin_iata/destination_iata/airline
        # are denormalized scalars for fast history queries only — never read them without
        # checking flight_info first.
        if row.input_mode == "flight_number" and row.flight_number:
            try:
                from app.services.flight_snapshot_service import (
                    build_flight_info_and_status,
                    get_selected_flight,
                )

                flight = get_selected_flight(
                    row.flight_number,
                    row.departure_date,
                    row.selected_departure_utc,
                )
                flight_info, flight_status = build_flight_info_and_status(flight)
                if flight_info:
                    row.flight_info = flight_info
                    row.flight_status = flight_status
                    row.origin_iata = flight_info.get("origin_iata")
                    row.destination_iata = flight_info.get("destination_iata")
                    row.airline = flight_info.get("airline")
            except Exception:
                logger.exception("Failed to populate flight_info for trip %s", trip_id)

        await db.commit()
        return {
            "status": "tracked",
            "trip_id": trip_id,
            "trip_count": user.trip_count,
            "flight_info": row.flight_info,
            "flight_status": row.flight_status,
        }

    raise HTTPException(status_code=400, detail=f"Cannot track trip in status {current}")


UNTRACKABLE_STATUSES = ("active", "en_route", "at_airport", "at_gate")


@router.post("/{trip_id}/untrack")
async def untrack_trip(
    trip_id: str,
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """Untrack a trip: reset to draft, clear phase fields, decrement trip_count."""
    if db is None:
        return {"status": "untracked", "trip_id": trip_id, "trip_count": 0}

    try:
        tid = _uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found")

    row = await db.get(TripRow, tid)
    if row is None or (row.user_id is not None and row.user_id != user.id):
        raise HTTPException(status_code=404, detail="Trip not found")

    current = row.trip_status
    if current not in UNTRACKABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot untrack trip in status '{current}'. Only active, en_route, at_airport, and at_gate trips can be untracked.",
        )

    # Reset to draft
    row.status = "draft"
    row.trip_status = "draft"

    # Clear phase fields
    row.projected_timeline = None
    row.last_pushed_leave_home_at = None
    row.push_count = 0
    row.morning_email_sent_at = None
    row.time_to_go_push_sent_at = None
    row.sms_count = 0
    row.actual_depart_at = None
    row.auto_completed = False
    row.feedback_requested_at = None

    # Decrement trip_count with floor of 0
    user.trip_count = max((user.trip_count or 0) - 1, 0)

    await db.commit()
    return {
        "status": "untracked",
        "trip_id": trip_id,
        "trip_count": user.trip_count,
    }


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
            TripRow.trip_status.in_(ACTIVE_STATUSES),
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
            "status": row.trip_status,
            "selected_departure_utc": row.selected_departure_utc,
            "preferences_json": row.preferences_json,
        }
    }


NON_COMPLETE_STATUSES = ("draft", "created", "active", "en_route", "at_airport", "at_gate")


@router.get("/active-list")
async def get_active_list(
    user: User = Depends(get_required_user),
    db=Depends(get_db),
):
    """Return all non-completed trips for the current user."""
    if db is None:
        return {"trips": []}

    stmt = (
        select(TripRow)
        .where(
            TripRow.user_id == user.id,
            TripRow.trip_status.in_(NON_COMPLETE_STATUSES),
        )
        .order_by(TripRow.departure_date.asc(), TripRow.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    return {
        "trips": [
            {
                "trip_id": str(row.id),
                "flight_number": row.flight_number,
                "airline": row.airline,
                "origin_iata": row.origin_iata,
                "destination_iata": row.destination_iata,
                "departure_date": row.departure_date,
                "status": row.trip_status,
                "projected_timeline": row.projected_timeline,
                "home_address": row.home_address,
            }
            for row in rows
        ]
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
            "origin_iata": row.origin_iata,
            "destination_iata": row.destination_iata,
            "airline": row.airline,
            "accuracy_delta_minutes": _compute_accuracy_delta(row, fb),
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
    """Update mutable fields on a trip. Draft/active only."""
    if db is None:
        return {"status": "updated", "trip_id": trip_id}

    try:
        tid = _uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found")

    row = await db.get(TripRow, tid)
    if row is None or (row.user_id is not None and row.user_id != user.id):
        raise HTTPException(status_code=404, detail="Trip not found")

    current = row.trip_status
    if current not in ("draft", "created", "active"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot edit trip in status '{current}'. Only pre-track and active trips can be edited.",
        )

    # Apply field updates
    if payload.home_address is not None:
        row.home_address = payload.home_address
    if payload.flight_number is not None:
        row.flight_number = payload.flight_number.strip().upper()
    if payload.departure_date is not None:
        row.departure_date = payload.departure_date

    # Preference-level updates (stored in preferences_json)
    import json as _json

    prefs_changed = False
    prefs = _json.loads(row.preferences_json) if row.preferences_json else {}
    if payload.transport_mode is not None:
        prefs["transport_mode"] = payload.transport_mode
        prefs_changed = True
    if payload.security_access is not None:
        prefs["security_access"] = payload.security_access
        prefs_changed = True
    if payload.buffer_preference is not None:
        prefs["gate_time_minutes"] = payload.buffer_preference
        prefs_changed = True
    if payload.bag_count is not None:
        prefs["bag_count"] = payload.bag_count
        prefs_changed = True
    if payload.traveling_with_children is not None:
        prefs["traveling_with_children"] = payload.traveling_with_children
        prefs_changed = True
    if payload.has_boarding_pass is not None:
        prefs["has_boarding_pass"] = payload.has_boarding_pass
        prefs_changed = True
    if payload.extra_time_minutes is not None:
        prefs["extra_time_minutes"] = payload.extra_time_minutes
        prefs_changed = True
    if payload.confidence_profile is not None:
        prefs["confidence_profile"] = payload.confidence_profile.value
        prefs_changed = True
    if prefs_changed:
        row.preferences_json = _json.dumps(prefs)

    # Recompute recommendation for active trips
    if current == "active":
        await db.flush()  # ensure get_trip_context sees updated fields
        try:
            from app.schemas.recommendations import RecommendationRequest
            from app.services.recommendation_service import compute_recommendation

            rec_response = await compute_recommendation(
                RecommendationRequest(trip_id=trip_id), user=user
            )
            timeline = _build_projected_timeline(rec_response, row.selected_departure_utc)
            if timeline:
                row.projected_timeline = timeline
        except Exception:
            logger.exception("Failed to recompute on update for trip %s", trip_id)

    await db.commit()
    return {"status": "updated", "trip_id": trip_id}


@router.get("/{trip_id}", response_model=TripDetailResponse)
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
        "status": row.trip_status,
        "selected_departure_utc": row.selected_departure_utc,
        "preferences_json": row.preferences_json,
        "origin_iata": row.origin_iata,
        "destination_iata": row.destination_iata,
        "airline": row.airline,
        "projected_timeline": row.projected_timeline,
        "flight_info": row.flight_info,
        "flight_status": row.flight_status,
        "latest_recommendation": row.latest_recommendation,
    }


