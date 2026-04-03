# CLAUDE.md

## Project Overview

AirBridge — door-to-gate departure decision engine. Computes personalized leave-home times by combining live flight data, traffic estimates, TSA wait models, and airport walking times into a segment timeline. Python 3.11+ / FastAPI / Pydantic v2 / async SQLAlchemy + PostgreSQL (Supabase). Deployed on Railway from `main`.

## Commands

```bash
PYTHONPATH=src uvicorn app.main:app --reload --port 8000   # dev server
PYTHONPATH=src pytest tests/ -v                             # all tests (140)
PYTHONPATH=src pytest tests/test_trips.py::TestFlightNumberMode::test_returns_201 -v  # single test
pip install -r requirements.txt                             # deps
PYTHONPATH=src alembic upgrade head                         # migrations
```

`PYTHONPATH=src` is required for all test/run commands.

## Key Directories

| Path | Purpose |
|------|---------|
| `src/app/api/routes/` | FastAPI routers |
| `src/app/api/middleware/auth.py` | JWT auth — `get_optional_user`, `get_required_user` |
| `src/app/schemas/` | Pydantic v2 models |
| `src/app/services/` | Business logic |
| `src/app/services/integrations/` | External API clients |
| `src/app/db/` | Async SQLAlchemy models + session |
| `src/app/data/airports/` | JSON airport profiles (add file to add airport) |
| `src/app/core/config.py` | Env-based settings; `errors.py` for `AppError` hierarchy |
| `tests/` | pytest, asyncio_mode=auto, httpx TestClient |

## Database

PostgreSQL on Supabase. Alembic migrations (currently at `0008_add_apple_user_id`). RLS enabled on all public tables. Models: Airport, User, Trip, Recommendation, DeviceToken, Feedback, Event.

```bash
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql   # before every production migration
```

## Deployment

Railway auto-deploys from `main`. After merge, run `PYTHONPATH=src alembic upgrade head` against production.

## Environment Variables

Required: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_KEY`, `JWT_SECRET`, `RAPIDAPI_KEY`, `GOOGLE_MAPS_API_KEY`
Optional: `SENTRY_DSN`, `FIREBASE_CREDENTIALS_JSON` (base64-encoded service account)

## API Endpoints

| Group | Route file | Endpoints |
|-------|-----------|-----------|
| Auth | `auth.py` | POST send-otp, verify-otp, social (Apple/Google) |
| Users | `users.py` | GET /me, PUT /preferences |
| Trips | `trips.py` | POST create, POST track, GET active, GET by id, PUT update |
| Recommendations | `recommendations.py` | POST compute, POST recompute (with preference overrides) |
| Flights | `flights.py` | GET lookup by number/date, GET route search |
| Devices | `devices.py` | POST register, DELETE unregister (FCM push tokens) |
| Events | `events.py` | POST analytics event |
| Health/Version | `health.py`, `version.py` | GET /health, GET /version |

Auth: Supabase handles phone OTP + social sign-in. Backend issues its own JWT (30-day, PyJWT).

## Services

| Service | Purpose |
|---------|---------|
| `recommendation_service.py` | Segment timeline engine — computes leave-home time from flight, traffic, TSA, walking |
| `polling_agent.py` | Background loop monitoring active trips, triggers recompute + push notifications |
| `notifications.py` | Push notification triggers (leave-by shift, time-to-go) |
| `trip_intake.py` | Validate + persist trip input (flight_number / route_search modes) |
| `trip_state.py` | Trip status FSM (created → active → en_route → completed) |
| `trial.py` | 3-trip free tier logic |
| `flight_snapshot_service.py` | Build flight snapshot from AeroDataBox or fallback |

## Integrations

| Integration | File | Purpose |
|-------------|------|---------|
| AeroDataBox | `aerodatabox.py` | Live flight data (via RapidAPI) |
| Google Maps | `google_maps.py` | Drive/transit time, geocoding, terminal coordinates |
| TSA model | `tsa_model.py` | Security wait estimates by airport/hour/day |
| Airport graph | `airport_graph.py` | Terminal-aware walking times (graph-based) |
| Airport defaults | `airport_defaults.py` | Flat walking-time defaults + airport timezone mapping |
| Airport cache | `airport_cache.py` | In-memory DB airport cache at startup |
| Firebase | `firebase.py` | FCM push notifications |
| Apple auth | `apple_auth.py` | Apple Sign In token verification |

## Key Patterns

- **Draft-then-track**: POST /trips creates draft, POST /trips/{id}/track promotes to active
- **Dual persistence**: In-memory FIFO (max 1000) + PostgreSQL. DB is primary
- **Confidence profiles**: safety (0.92), sweet (0.85), risk (0.70)
- **Preference overrides**: `_effective_context()` merges overrides into trip context for recompute
- **Notification timezone**: Polling agent converts UTC leave-home times to local using airport timezone

## Coding Conventions

- Additive API changes only — don't break existing clients
- All tests must pass before every commit
- One task per prompt
- Read files before editing
