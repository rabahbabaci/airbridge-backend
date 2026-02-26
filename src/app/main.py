from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.routes import health, recommendations, trips, version
from app.core.config import settings
from app.core.errors import AppError, app_error_handler, validation_error_handler

app = FastAPI(
    title="AirBridge API",
    description="Door-to-gate departure decision engine",
    version=settings.app_version,
)


@app.get("/")
def root():
    return {
        "name": "AirBridge API",
        "docs": "/docs",
        "health": "/health",
        "version": "/version",
        "trips": "/v1/trips",
        "recommendations": "/v1/recommendations",
    }


app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

app.include_router(health.router)
app.include_router(version.router)
app.include_router(trips.router, prefix="/v1")
app.include_router(recommendations.router, prefix="/v1")
