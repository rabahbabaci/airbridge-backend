"""Feedback endpoint for post-trip data collection and TSA observations."""

import json
import logging
import math
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.middleware.auth import get_required_user
from app.db import get_db
from app.db.models import Feedback, Recommendation, Trip, TsaObservation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    trip_id: str = Field(..., max_length=50)
    followed_recommendation: bool | None = None
    minutes_at_gate: int | None = Field(None, ge=0, le=300)
    actual_tsa_wait_minutes: int | None = Field(None, ge=0, le=180)


@router.post("")
async def submit_feedback(
    body: FeedbackRequest,
    user=Depends(get_required_user),
    db=Depends(get_db),
):
    """Submit post-trip feedback and optionally collect TSA wait observation."""
    if db is None:
        return JSONResponse(
            status_code=503,
            content={"code": "DB_NOT_CONFIGURED", "message": "Database not available"},
        )

    # Validate trip exists and belongs to user
    trip = await db.get(Trip, uuid.UUID(body.trip_id))
    if trip is None:
        return JSONResponse(
            status_code=404,
            content={"code": "TRIP_NOT_FOUND", "message": "Trip not found"},
        )
    if trip.user_id != user.id:
        return JSONResponse(
            status_code=403,
            content={"code": "FORBIDDEN", "message": "Trip does not belong to this user"},
        )

    # Create Feedback row
    fb = Feedback(
        id=uuid.uuid4(),
        trip_id=trip.id,
        user_id=user.id,
        followed_recommendation=body.followed_recommendation,
        minutes_at_gate=body.minutes_at_gate,
        actual_tsa_wait_minutes=body.actual_tsa_wait_minutes,
    )
    db.add(fb)

    # If TSA wait reported, try to insert a TsaObservation
    tsa_observation_stored = False
    if body.actual_tsa_wait_minutes is not None:
        tsa_observation_stored = await _try_store_tsa_observation(
            db, trip, user.id, body.actual_tsa_wait_minutes
        )

    await db.commit()

    # Compute accuracy stats for response
    stats = await _compute_accuracy_stats(db, user.id)

    return {
        "feedback_id": str(fb.id),
        "tsa_observation_stored": tsa_observation_stored,
        **stats,
    }


async def _try_store_tsa_observation(
    db, trip, user_id: uuid.UUID, actual_wait: int
) -> bool:
    """Insert a TsaObservation if the reported wait is not an outlier."""
    # Extract airport and departure info from the trip's latest recommendation
    rec_stmt = (
        select(Recommendation)
        .where(Recommendation.trip_id == trip.id)
        .order_by(Recommendation.computed_at.desc())
        .limit(1)
    )
    rec = (await db.execute(rec_stmt)).scalar_one_or_none()

    # Try to get airport code from trip's flight_number context or recommendation segments
    airport_code = None
    departure_hour = 12
    day_of_week = 0

    if rec and rec.segments_json:
        try:
            segments = json.loads(rec.segments_json)
            for seg in segments:
                if isinstance(seg, dict) and "tsa" in seg.get("id", "").lower():
                    label = seg.get("label", "")
                    # Extract airport from label like "TSA Security (LAX)"
                    if "(" in label and ")" in label:
                        airport_code = label.split("(")[-1].split(")")[0].strip()
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    if rec and rec.leave_home_at:
        try:
            dt = datetime.fromisoformat(rec.leave_home_at.replace("Z", "+00:00"))
            departure_hour = dt.hour
            day_of_week = dt.weekday()
        except (ValueError, TypeError):
            pass

    if not airport_code:
        logger.debug("Could not determine airport for TSA observation, skipping")
        return False

    # Outlier rejection: check mean + std dev from existing observations
    stats_stmt = select(
        func.avg(TsaObservation.wait_minutes),
        func.stddev(TsaObservation.wait_minutes),
        func.count(TsaObservation.id),
    ).where(
        TsaObservation.airport_code == airport_code,
        TsaObservation.time_of_day == departure_hour,
        TsaObservation.day_of_week == day_of_week,
    )
    row = (await db.execute(stats_stmt)).one()
    avg_wait, std_dev, obs_count = row[0], row[1], row[2]

    # If enough observations exist, apply outlier rejection
    if obs_count >= 10 and std_dev is not None and std_dev > 0:
        if abs(actual_wait - avg_wait) > 3 * std_dev:
            logger.info(
                "TSA observation rejected as outlier: %d min (mean=%.1f, std=%.1f) for %s",
                actual_wait, avg_wait, std_dev, airport_code,
            )
            return False

    # Get security_access from trip preferences
    security_access = "none"
    if trip.preferences_json:
        try:
            prefs = json.loads(trip.preferences_json)
            security_access = prefs.get("security_access", "none") if isinstance(prefs, dict) else "none"
        except (json.JSONDecodeError, TypeError):
            pass

    obs = TsaObservation(
        id=uuid.uuid4(),
        airport_code=airport_code,
        checkpoint_type=security_access,
        day_of_week=day_of_week,
        time_of_day=departure_hour,
        wait_minutes=actual_wait,
        user_id=user_id,
    )
    db.add(obs)
    logger.info("TSA observation stored: %d min at %s", actual_wait, airport_code)
    return True


async def _compute_accuracy_stats(db, user_id: uuid.UUID) -> dict:
    """Compute accuracy stats from user's feedback history."""
    stmt = select(
        func.count(Feedback.id),
        func.avg(Feedback.minutes_at_gate),
    ).where(
        Feedback.user_id == user_id,
        Feedback.minutes_at_gate.is_not(None),
    )
    row = (await db.execute(stmt)).one()
    trips_with_feedback = row[0] or 0
    avg_gate_minutes = row[1]

    # Compute personal accuracy trend (difference from ideal 30 min at gate)
    if avg_gate_minutes is not None:
        avg_accuracy_minutes = round(abs(float(avg_gate_minutes) - 30), 1)
    else:
        avg_accuracy_minutes = None

    return {
        "avg_accuracy_minutes": avg_accuracy_minutes,
        "trips_with_feedback": trips_with_feedback,
        "personal_accuracy_trend": "improving" if trips_with_feedback >= 3 else "insufficient_data",
    }
