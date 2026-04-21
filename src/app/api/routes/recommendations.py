import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.middleware.auth import get_optional_user
from app.core.errors import (
    AppError,
    UpstreamRateLimitedError,
    UpstreamUnavailableError,
)
from app.db import get_db
from app.db.models import Trip as TripRow, User
from app.schemas.recommendations import (
    RecommendationRecomputeRequest,
    RecommendationRequest,
    RecommendationResponse,
)
from app.services.integrations.aerodatabox import (
    AeroDataBoxError,
    AeroDataBoxRateLimited,
)
from app.services.recommendation_service import (
    compute_recommendation,
    recompute_recommendation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _translate_upstream(exc: AeroDataBoxError) -> AppError:
    """Translate an integration-layer exception to an HTTP-layer AppError.

    Recommendation routes don't distinguish NotFound from other upstream
    failures — if we got this far, we've already confirmed the trip exists
    in our DB, so a 404 from ADB for the flight is still a 503 to the user
    (the upstream can't serve us data about a flight we know exists).
    """
    if isinstance(exc, AeroDataBoxRateLimited):
        return UpstreamRateLimitedError()
    return UpstreamUnavailableError()


@router.post("", response_model=RecommendationResponse, status_code=200)
async def post_recommendation(
    payload: RecommendationRequest,
    user: User | None = Depends(get_optional_user),
) -> RecommendationResponse:
    """Compute a leave-home recommendation for the given trip."""
    try:
        response = await compute_recommendation(payload, user=user, strict=True)
    except AeroDataBoxError as e:
        raise _translate_upstream(e) from e
    if response is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return response


@router.post("/recompute", response_model=RecommendationResponse, status_code=200)
async def post_recommendation_recompute(
    payload: RecommendationRecomputeRequest,
    user: User | None = Depends(get_optional_user),
    db=Depends(get_db),
) -> RecommendationResponse:
    """Recompute recommendation for an existing trip; optionally pass preference_overrides or home_address."""
    # Persist home_address BEFORE recompute so the engine picks it up via get_trip_context()
    if payload.home_address is not None and db is not None:
        try:
            tid = uuid.UUID(payload.trip_id)
            trip_row = await db.get(TripRow, tid)
            if trip_row is not None:
                trip_row.home_address = payload.home_address
                await db.commit()
        except Exception:
            logger.exception("Failed to persist home_address for trip %s", payload.trip_id)

    try:
        response = await recompute_recommendation(payload, user=user, strict=True)
    except AeroDataBoxError as e:
        raise _translate_upstream(e) from e
    if response is None:
        raise HTTPException(status_code=404, detail="Trip not found")

    # Persist preference overrides to the Trip row (best-effort)
    if payload.preference_overrides is not None and db is not None:
        try:
            tid = uuid.UUID(payload.trip_id)
            trip_row = await db.get(TripRow, tid)
            if trip_row is not None:
                existing = {}
                if trip_row.preferences_json:
                    existing = json.loads(trip_row.preferences_json)
                overrides = payload.preference_overrides.model_dump(exclude_none=True)
                existing.update(overrides)
                trip_row.preferences_json = json.dumps(existing)
                await db.commit()
        except Exception:
            logger.exception("Failed to persist preference overrides for trip %s", payload.trip_id)

    return response
