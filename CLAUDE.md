# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Airbridge is a door-to-gate departure decision engine for airport trips. It computes personalized leave-home times by combining live flight data, traffic estimates, TSA wait models, and airport walking times into a segment-by-segment timeline. Python 3.11+ / FastAPI / Pydantic v2 / PostgreSQL (async SQLAlchemy) / Supabase auth. Covers 10 US airports (SFO, OAK, SJC, LAX, JFK, ORD, ATL, DFW, SEA, BOS).

## Commands

```bash
# Run dev server
PYTHONPATH=src uvicorn app.main:app --reload --port 8000

# Run all tests
PYTHONPATH=src pytest tests/ -v

# Run single test file
PYTHONPATH=src pytest tests/test_trips.py -v

# Run single test
PYTHONPATH=src pytest tests/test_trips.py::TestFlightNumberMode::test_returns_201 -v

# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head
```

No linter or formatter is configured in the project yet.

## Architecture

**Request flow**: Route handler → Schema validation (Pydantic) → Service layer → Integration clients → Response model

### Key layers

- **`src/app/api/routes/`** — FastAPI routers. `trips.py` (POST /v1/trips, GET /active, GET /{id}), `recommendations.py` (compute/recompute), `flights.py` (flight lookup/search), `auth.py` (OTP + social sign-in), `users.py` (profile + preferences), `devices.py` (push token registration), `events.py` (analytics).
- **`src/app/api/middleware/`** — JWT auth middleware. `auth.py` provides `get_optional_user` and `get_required_user` dependencies.
- **`src/app/schemas/`** — Pydantic v2 models for request/response validation. Trip input uses a discriminated union on `input_mode` field (flight_number vs route_search).
- **`src/app/services/`** — Business logic. `trip_intake.py` validates/normalizes and persists trips (in-memory FIFO + PostgreSQL). `recommendation_service.py` is the core engine that builds a segment timeline (transport → airport → baggage → security → gate) with three confidence profiles (safety: 0.92, sweet: 0.85, risk: 0.70). `trial.py` handles free tier logic (3-trip trial).
- **`src/app/services/integrations/`** — External API clients. AeroDataBox for flight status, Google Maps for travel time, TSA model for security wait estimates, airport graph/defaults for walking times. All integrations have deterministic fallbacks when APIs are unavailable.
- **`src/app/db/`** — Database layer. `__init__.py` sets up async SQLAlchemy engine and session factory. `models.py` defines ORM models (Airport, User, Trip, Recommendation, DeviceToken, Feedback, Event).
- **`src/app/services/`** — Also includes `trip_state.py` (status state machine), `notifications.py` (push trigger engine), `polling_agent.py` (background trip monitor).
- **`src/app/services/integrations/`** — Also includes `airport_cache.py` (in-memory DB cache loaded at startup), `firebase.py` (FCM push notifications).
- **`src/app/data/airports/`** — JSON profiles per airport. Adding a new airport requires only a new JSON file, no code changes.
- **`src/app/core/`** — `config.py` loads settings from env vars; `errors.py` defines `AppError` hierarchy with structured JSON responses.

### Important patterns

- **Preference overrides**: `_effective_context()` in recommendation_service merges user preference overrides (security_access, has_boarding_pass, etc.) into the trip context for recompute.
- **Dual persistence**: Trips persist to both an in-memory FIFO cache (max 1000) and PostgreSQL. DB is primary; in-memory serves as fallback for resilience.
- **Auth flow**: Supabase handles phone OTP and social sign-in (Apple/Google). Backend verifies and issues its own JWT (30-day expiry) via PyJWT.
- **Pydantic validation**: Field validators normalize input (trim whitespace, uppercase IATA codes). Model validators handle cross-field rules.
- **Error shape**: All errors return `{"code": "...", "message": "...", "details": [...]}`.

## Testing

Tests use pytest with `asyncio_mode = "auto"`. Test client is FastAPI's TestClient (via httpx). Tests are organized by endpoint in class-based groups. `PYTHONPATH=src` is required for all test/run commands.

## Environment Variables

Required in `.env`: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_KEY`, `JWT_SECRET`, `RAPIDAPI_KEY`, `GOOGLE_MAPS_API_KEY`. Optional: `SENTRY_DSN`, `FIREBASE_CREDENTIALS_JSON` (base64-encoded service account).

## Database Backup Protocol

Before running any Alembic migration against production, take a backup:
```bash
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```

Run this from your local machine or Railway shell. Verify the backup file is non-empty before proceeding with the migration.

## API Endpoints (for frontend reference)

Auth: all endpoints returning `token` use JWT Bearer auth (`Authorization: Bearer <token>`).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /v1/auth/send-otp | — | Send phone OTP |
| POST | /v1/auth/verify-otp | — | Verify OTP → returns `{user_id, token, trip_count, tier}` |
| POST | /v1/auth/social | — | Apple/Google sign-in → same response shape |
| GET | /v1/users/me | required | User profile + preferences |
| PUT | /v1/users/preferences | required | Update preferences |
| POST | /v1/trips | optional | Create trip (flight_number or route_search mode) → `TripContext` |
| GET | /v1/trips/active | required | User's current active trip or null |
| GET | /v1/trips/{trip_id} | required | Single trip by ID (must own it) |
| POST | /v1/recommendations/compute | optional | Compute leave-home recommendation |
| POST | /v1/recommendations/recompute | optional | Recompute with preference overrides |
| GET | /v1/flights/{flight_number}/{date} | — | Flight lookup |
| GET | /v1/flights/search | — | Route search (origin, destination, date, filters) |
| POST | /v1/devices/register | required | Register push notification token |
| DELETE | /v1/devices/unregister | required | Remove push token |
| POST | /v1/events | optional | Record analytics event |
| GET | /health | — | Health check |
| GET | /version | — | App version |
