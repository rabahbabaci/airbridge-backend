# CLAUDE.md

## Project Overview

AirBridge — door-to-gate departure decision engine. Computes personalized leave-home times by combining live flight data, traffic estimates, TSA wait models, and airport walking times into a segment timeline. Python 3.11+ / FastAPI / Pydantic v2 / async SQLAlchemy + PostgreSQL (Supabase). Deployed on Railway from `main`.

## Commands

```bash
PYTHONPATH=src uvicorn app.main:app --reload --port 8000   # dev server
PYTHONPATH=src pytest tests/ -v                             # all tests (244)
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

PostgreSQL on Supabase. Alembic migrations (currently at `0011_sprint7_trip_flight_columns`). RLS enabled on all public tables. Models: Airport, User, Trip, Recommendation, DeviceToken, Feedback, Event, TsaObservation.

```bash
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql   # before every production migration
```

## Deployment

Railway auto-deploys from `main`. After merge, run `PYTHONPATH=src alembic upgrade head` against production.

## Environment Variables

Required: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_KEY`, `JWT_SECRET`, `RAPIDAPI_KEY`, `GOOGLE_MAPS_API_KEY`
Optional: `SENTRY_DSN`, `FIREBASE_CREDENTIALS_JSON` (base64-encoded service account)
Stripe: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_MONTHLY`, `STRIPE_PRICE_ANNUAL`
Email: `SENDGRID_API_KEY`, `FROM_EMAIL`
SMS: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`
TSA API: `TSA_WAIT_TIMES_API_KEY`

## API Endpoints

| Group | Route file | Endpoints |
|-------|-----------|-----------|
| Auth | `auth.py` | POST send-otp, verify-otp, social (Apple/Google) |
| Users | `users.py` | GET /me, PUT /preferences, DELETE /me (account deletion) |
| Trips | `trips.py` | POST create, POST track, POST untrack, GET active, GET active-list, GET by id, PUT update, GET history |
| Subscriptions | `subscriptions.py` | POST checkout, POST webhook, GET status, POST portal |
| Feedback | `feedback.py` | POST feedback (with TSA observation collection) |
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
| `notifications/` | Push (FCM), email (SendGrid), SMS (Twilio) notification channels |
| `trip_intake.py` | Validate + persist trip input (flight_number / route_search modes) |
| `trip_state.py` | Trip status FSM (draft → created → active → en_route → at_airport → at_gate → complete) |
| `trial.py` | 3-trip free tier logic |
| `flight_snapshot_service.py` | Build flight snapshot from AeroDataBox or fallback |

## Integrations

| Integration | File | Purpose |
|-------------|------|---------|
| AeroDataBox | `aerodatabox.py` | Live flight data (via RapidAPI) |
| Google Maps | `google_maps.py` | Drive/transit time, geocoding, terminal coordinates |
| TSA model | `tsa_model.py` | Three-layer blended TSA estimates (static + API + user feedback) |
| TSA API | `tsa_api.py` | TSAWaitTimes.com live data with 15-min cache |
| Airport graph | `airport_graph.py` | Terminal-aware walking times (graph-based) |
| Airport defaults | `airport_defaults.py` | Flat walking-time defaults + airport timezone mapping |
| Airport cache | `airport_cache.py` | In-memory DB airport cache at startup |
| Firebase | `firebase.py` | FCM push notifications |
| Apple auth | `apple_auth.py` | Apple Sign In token verification |

## Key Patterns

- **Draft-then-track**: POST /trips creates draft, POST /trips/{id}/track promotes to active + computes projected_timeline
- **Dual persistence**: In-memory FIFO (max 1000) + PostgreSQL. DB is primary
- **Confidence profiles**: safety (0.92), sweet (0.85), risk (0.70)
- **Preference overrides**: `_effective_context()` merges overrides into trip context for recompute
- **Notification timezone**: Polling agent converts UTC leave-home times to local using airport timezone
- **Multi-channel notifications**: FCM push (existing) + SendGrid email (morning briefing) + Twilio SMS (time-to-go escalation, Pro only)
- **Smart passive tracking**: Time-based state advancement using projected_timeline milestones + interaction signals from events table. No location permissions required.
- **Phase-aware polling**: active=full recompute, en_route=cancellation/gate only, at_airport/at_gate=minimal, complete=stop
- **TSA blending**: Static baselines (weight 0.3-1.0) + live API (0.5) + user feedback observations (0.2). Weights redistribute when layers unavailable.
- **Subscription model**: Stripe checkout/webhook/portal. Trial = first 3 trips free. Pro = subscription_status="active"
- **Data flywheel**: Post-trip feedback collects actual TSA wait times → TsaObservation → feeds back into TSA blending model

## Coding Conventions

- Additive API changes only — don't break existing clients
- All tests must pass before every commit
- One task per prompt
- Read files before editing

## Sprint 7 Context (April 2026)

### Schema Changes
- Trip model now stores `origin_iata`, `destination_iata`, `airline` (migration 0011). Populated during track (flight_number mode, from AeroDataBox) or intake (route_search mode, from request payload). **Historical pre-migration trips return null — frontend must handle null gracefully in History tab.**

### Trip Status: `created`
- Valid state between `draft` and `active` in the FSM. Auto-activated by polling agent when departure is within 24 hours. Included in active-list and active queries. Trips created via the API start as `draft`; `created` exists for legacy/direct-insert compatibility.

### New/Modified Endpoints
- **GET /v1/trips/active-list**: Returns all non-completed trips (draft, created, active, en_route, at_airport, at_gate). Ordered by departure_date ASC.
- **PUT /v1/trips/{id}** (expanded): Editable fields: flight_number, departure_date, home_address, transport_mode, security_access, buffer_preference. Trip must be in `draft` or `active` status — returns 409 for locked states (en_route, at_airport, at_gate, complete). Active trips trigger recommendation recompute + projected_timeline update.
- **POST /v1/trips/{id}/untrack**: Resets tracked trip to draft. Clears projected_timeline and all 9 phase/notification fields. Decrements `user.trip_count` with floor of 0 (fairness contract — users aren't punished for fixing mistakes). Allowed for active, en_route, at_airport, at_gate. Returns 409 for draft or complete.
- **GET /v1/subscriptions/status**: Now includes `current_period_end` (Unix timestamp from Stripe API). Null for trial users or on Stripe error.
- **GET /v1/trips/history**: Row shape enriched with `origin_iata`, `destination_iata`, `airline`, `accuracy_delta_minutes` (actual gate wait minus predicted gate buffer; positive = early, negative = late, null if no feedback/timeline).

### Ownership Pattern
- New/modified trip endpoints use 404 (not 403) for other users' trips — don't leak trip existence.

### Testing Principle (Sprint 7 rule)
- **No over-mocking.** Tests must exercise real code paths with realistic fixtures. The Sprint 6 Stripe webhook bug passed tests because `async_session_factory` was mocked to None, short-circuiting the buggy code path. Sprint 7 adds async SQLite test DB (`aiosqlite`) for real query testing. `db=None` tests are thin smoke only — they do not count as business logic coverage.
