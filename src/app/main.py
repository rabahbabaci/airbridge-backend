from fastapi import FastAPI

from app.api.routes import health, recommendations, trips

app = FastAPI(
    title="AirBridge API",
    description="Door-to-gate departure decision engine",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(trips.router, prefix="/v1")
app.include_router(recommendations.router, prefix="/v1")
