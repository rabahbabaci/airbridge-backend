import asyncio
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.api.routes import auth, devices, events, feedback, flights, health, recommendations, subscriptions, trips, users, version
from app.core.config import settings
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.services.integrations.airport_cache import load_airport_cache
from app.services.integrations.firebase import init_firebase
from app.services.polling_agent import start_polling_agent

if settings.sentry_dsn and "PYTEST_CURRENT_TEST" not in os.environ:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if settings.database_url:
        from app.db import Base, engine

        if engine is not None:
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                logger.info("Database connected — tables ensured via create_all")
            except Exception as e:
                logger.warning("Database connection failed, running in in-memory mode: %s", e)
    else:
        logger.info("No DATABASE_URL configured — running in-memory mode")
    await load_airport_cache()
    init_firebase()
    if settings.enable_polling_agent:
        polling_task = asyncio.create_task(start_polling_agent())
        app.state.polling_task = polling_task
    yield
    # Shutdown
    if hasattr(app.state, "polling_task"):
        app.state.polling_task.cancel()
        try:
            await app.state.polling_task
        except asyncio.CancelledError:
            pass
    if settings.database_url:
        from app.db import engine

        if engine is not None:
            await engine.dispose()


app = FastAPI(
    title="AirBridge API",
    description="Door-to-gate departure decision engine",
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://airbridge.live",       # Production web
        "capacitor://localhost",         # Native iOS app (Capacitor)
        "http://localhost:5173",         # Vite dev server
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


from app.core.rate_limit import limiter

app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"code": "RATE_LIMITED", "message": "Too many requests. Please try again later."},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

app.include_router(health.router)
app.include_router(version.router)
app.include_router(trips.router, prefix="/v1")
app.include_router(recommendations.router, prefix="/v1")
app.include_router(flights.router, prefix="/v1")
app.include_router(auth.router, prefix="/v1")
app.include_router(events.router, prefix="/v1/events")
app.include_router(users.router, prefix="/v1/users")
app.include_router(devices.router, prefix="/v1/devices")
app.include_router(subscriptions.router, prefix="/v1")
app.include_router(feedback.router, prefix="/v1")
