# CLAUDE.md

## Project Overview

Airbridge — door-to-gate departure decision engine. Computes personalized leave-home times by combining live flight data, traffic estimates, TSA wait models, and airport walking times into a segment timeline. Python 3.11+ / FastAPI / Pydantic v2 / async SQLAlchemy + PostgreSQL / Supabase auth. 10 US airports (SFO, OAK, SJC, LAX, JFK, ORD, ATL, DFW, SEA, BOS).

## Commands

```bash
PYTHONPATH=src uvicorn app.main:app --reload --port 8000   # dev server
PYTHONPATH=src pytest tests/ -v                             # all tests
PYTHONPATH=src pytest tests/test_trips.py -v                # single file
PYTHONPATH=src pytest tests/test_trips.py::TestFlightNumberMode::test_returns_201 -v
pip install -r requirements.txt                             # deps
alembic upgrade head                                        # migrations
```

`PYTHONPATH=src` is required for all test/run commands. No linter configured yet.

## Architecture

**Request flow**: Route → Pydantic schema → Service → Integration client → Response

### Layers

| Layer | Path | What it does |
|-------|------|-------------|
| Routes | `src/app/api/routes/` | FastAPI routers: trips, recommendations, flights, auth, users, devices, events, health, version |
| Middleware | `src/app/api/middleware/auth.py` | JWT auth — `get_optional_user`, `get_required_user` dependencies |
| Schemas | `src/app/schemas/` | Pydantic v2 models. Trip input is a discriminated union on `input_mode` (flight_number / route_search) |
| Services | `src/app/services/` | `trip_intake.py` (validate + persist), `recommendation_service.py` (segment timeline engine), `trip_state.py` (status FSM), `notifications.py` (push triggers), `polling_agent.py` (background monitor), `trial.py` (3-trip free tier) |
| Integrations | `src/app/services/integrations/` | AeroDataBox (flights), Google Maps (travel time), TSA model, airport graph/defaults (walking), airport_cache (in-memory DB cache at startup), Firebase (FCM push) |
| DB | `src/app/db/` | Async SQLAlchemy engine + session. Models: Airport, User, Trip, Recommendation, DeviceToken, Feedback, Event |
| Data | `src/app/data/airports/` | JSON profiles per airport — add file to add airport, no code changes |
| Config | `src/app/core/config.py` | Env-based settings. `errors.py` defines `AppError` hierarchy |

### Key patterns

- **Preference overrides**: `_effective_context()` merges user overrides (security_access, has_boarding_pass, etc.) into trip context for recompute.
- **Draft-then-track flow**: `POST /trips` creates draft, `POST /trips/{id}/track` promotes to active and increments trip_count.
- **Dual persistence**: Trips persist to in-memory FIFO (max 1000) + PostgreSQL. DB is primary; in-memory is fallback.
- **Confidence profiles**: safety (0.92), sweet (0.85), risk (0.70) — each with different gate buffer minutes.
- **Auth**: Supabase handles phone OTP + social sign-in (Apple/Google). Backend issues its own JWT (30-day, PyJWT).
- **Error shape**: `{"code": "...", "message": "...", "details": [...]}`. Hierarchy: `AppError` base, `UnsupportedModeError`.
- **Startup**: loads airport cache, inits Firebase, starts polling agent as background task, creates DB tables if needed.

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | /v1/auth/send-otp | — | Send phone OTP |
| POST | /v1/auth/verify-otp | — | Verify OTP → `{user_id, token, trip_count, tier}` |
| POST | /v1/auth/social | — | Apple/Google sign-in |
| GET | /v1/users/me | required | Profile + preferences |
| PUT | /v1/users/preferences | required | Update preferences |
| POST | /v1/trips | optional | Create draft trip |
| POST | /v1/trips/{trip_id}/track | optional | Promote draft → active |
| GET | /v1/trips/active | required | Current active trip |
| GET | /v1/trips/{trip_id} | required | Single trip (ownership check) |
| PUT | /v1/trips/{trip_id} | required | Update trip (home_address) |
| POST | /v1/recommendations/compute | optional | Compute recommendation |
| POST | /v1/recommendations/recompute | optional | Recompute with overrides |
| GET | /v1/flights/{flight_number}/{date} | — | Flight lookup |
| GET | /v1/flights/search | — | Route search |
| POST | /v1/devices/register | required | Register push token |
| DELETE | /v1/devices/unregister | required | Remove push token |
| POST | /v1/events | optional | Analytics event |
| GET | /health | — | Health check |
| GET | /version | — | App version |

## Environment Variables

Required: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_KEY`, `JWT_SECRET`, `RAPIDAPI_KEY`, `GOOGLE_MAPS_API_KEY`
Optional: `SENTRY_DSN`, `FIREBASE_CREDENTIALS_JSON` (base64-encoded service account)

## Testing

pytest with `asyncio_mode = "auto"`. FastAPI TestClient via httpx. Class-based test groups per endpoint.

## Database

Migrations via Alembic (7 versions through `0007_enable_rls_all_tables`). RLS enabled on all public tables.

Before running migrations against production:
```bash
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```
