from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(tags=["version"])


class VersionResponse(BaseModel):
    app_name: str
    version: str
    environment: str


@router.get("/version", response_model=VersionResponse)
def get_version() -> VersionResponse:
    """Returns app name, version string, and current environment."""
    return VersionResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
