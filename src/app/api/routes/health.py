from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])

_VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Liveness check."""
    return HealthResponse(status="ok", version=_VERSION)
