from fastapi import APIRouter, Depends, HTTPException

from app.api.middleware.auth import get_optional_user
from app.db.models import User
from app.schemas.recommendations import (
    RecommendationRecomputeRequest,
    RecommendationRequest,
    RecommendationResponse,
)
from app.services.recommendation_service import (
    compute_recommendation,
    recompute_recommendation,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("", response_model=RecommendationResponse, status_code=200)
async def post_recommendation(
    payload: RecommendationRequest,
    user: User | None = Depends(get_optional_user),
) -> RecommendationResponse:
    """Compute a leave-home recommendation for the given trip."""
    response = await compute_recommendation(payload, user=user)
    if response is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return response


@router.post("/recompute", response_model=RecommendationResponse, status_code=200)
async def post_recommendation_recompute(
    payload: RecommendationRecomputeRequest,
    user: User | None = Depends(get_optional_user),
) -> RecommendationResponse:
    """Recompute recommendation for an existing trip; optionally pass preference_overrides."""
    response = await recompute_recommendation(payload, user=user)
    if response is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    return response
