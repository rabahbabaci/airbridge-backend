from fastapi import APIRouter

from app.schemas.recommendations import (
    RecommendationRecomputeRequest,
    RecommendationRequest,
    RecommendationResponse,
)
from app.services.recommendation_service import compute_recommendation, recompute_recommendation

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("", response_model=RecommendationResponse, status_code=200)
def post_recommendation(payload: RecommendationRequest) -> RecommendationResponse:
    """Compute a leave-home recommendation for the given trip."""
    return compute_recommendation(payload)


@router.post("/recompute", response_model=RecommendationResponse, status_code=200)
def post_recommendation_recompute(payload: RecommendationRecomputeRequest) -> RecommendationResponse:
    """Recompute recommendation for an existing trip."""
    return recompute_recommendation(payload)
