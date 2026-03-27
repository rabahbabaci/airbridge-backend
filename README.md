<p align="center">
  <h1 align="center">AirBridge Backend</h1>
  <p align="center">The door-to-gate departure decision engine.</p>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/PostgreSQL-async-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL"></a>
  <a href="https://supabase.com/"><img src="https://img.shields.io/badge/Supabase-auth-3FCF8E?logo=supabase&logoColor=white" alt="Supabase"></a>
  <a href="https://sentry.io/"><img src="https://img.shields.io/badge/Sentry-monitoring-362D59?logo=sentry&logoColor=white" alt="Sentry"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey.svg" alt="License"></a>
</p>

---

AirBridge eliminates the guesswork of airport timing. Given a flight and a home address, it computes a personalized **leave-home-by** time backed by real-time flight data, traffic estimates, TSA wait models, and terminal walking distances — broken down into a segment-by-segment timeline so travelers never wait too long and never miss a flight.

## Table of Contents

- [How It Works](#how-it-works)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [API Reference](#api-reference)
- [Database](#database)
- [Testing](#testing)
- [Airport Coverage](#airport-coverage)
- [Environment Variables](#environment-variables)
- [Related Repos](#related-repos)
- [License](#license)

---

## How It Works

AirBridge breaks every airport trip into **timed segments**, each informed by live data and user preferences:

```
Home ──► Transport ──► Parking/Curbside ──► Check-in ──► TSA ──► Walk to Gate ──► Board
         (Google       (airport graph       (bag count,    (percentile     (terminal
          Maps +        walking model)       boarding       model +         graph or
          traffic)                           pass)          PreCheck/       defaults)
                                                            CLEAR)
```

Three **confidence profiles** control the risk/comfort tradeoff:

| Profile | Approach | TSA Percentile | Gate Buffer |
|---------|----------|----------------|-------------|
| **Safety** | Maximum certainty | p80 | 30 min |
| **Sweet** | Balanced | p50 | 15 min |
| **Risk** | Aggressive | p25 | 0 min |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Framework** | FastAPI 0.115+ |
| **Language** | Python 3.11+ |
| **Validation** | Pydantic v2 |
| **Database** | PostgreSQL (async via SQLAlchemy 2.0 + asyncpg) |
| **Migrations** | Alembic |
| **Auth** | Supabase (phone OTP + Google/Apple social) + PyJWT |
| **Flight Data** | AeroDataBox (via RapidAPI) |
| **Traffic** | Google Maps Distance Matrix API |
| **Monitoring** | Sentry (error tracking + performance) |
| **Testing** | pytest + httpx + pytest-asyncio |
| **Server** | Uvicorn (ASGI) |

---

## Architecture

```
Mobile / Web Client
       │
       ▼
  ┌─────────────────────────────────────────────────┐
  │                  FastAPI App                     │
  │                                                  │
  │  Auth          Trips        Recommendations      │
  │  (OTP,         (intake,     (compute,            │
  │   Social)       search)      recompute)          │
  │                                                  │
  │  Users         Flights      Events               │
  │  (profile,     (status,     (analytics)          │
  │   prefs)        search)                          │
  └──────────┬──────────┬──────────┬────────────────┘
             │          │          │
       ┌─────┘    ┌─────┘    ┌────┘
       ▼          ▼          ▼
  ┌─────────┐ ┌────────┐ ┌──────────────────────┐
  │Supabase │ │ Postgres│ │  Recommendation      │
  │  Auth   │ │   (DB)  │ │  Engine              │
  └─────────┘ └────────┘ │                       │
                          │ AeroDataBox (flights) │
                          │ Google Maps (traffic) │
                          │ TSA Model (security)  │
                          │ Airport Graphs (walk) │
                          └──────────────────────┘
```

**Request flow:** Route handler -> Pydantic schema validation -> Service layer -> Integration clients -> Structured response

**Key design decisions:**
- **Fallback-first integrations** — Every external API call has a deterministic fallback so recommendations always resolve, even when third-party services are down.
- **Airport-agnostic expansion** — Adding a new airport requires a JSON config file, not code changes.
- **Segment-based timeline** — Recommendations decompose into individually explainable segments with duration, label, and advice.
- **Preference layering** — User-saved preferences -> trip-level preferences -> request-time overrides, merged at compute time.

---

## Project Structure

```
src/app/
├── main.py                              # App entry, CORS, Sentry init, route registration
├── core/
│   ├── config.py                        # Environment-driven settings (Settings class)
│   └── errors.py                        # AppError hierarchy + structured JSON handlers
├── api/
│   ├── routes/
│   │   ├── health.py                    # GET  /health
│   │   ├── version.py                   # GET  /version
│   │   ├── trips.py                     # POST /v1/trips
│   │   ├── recommendations.py           # POST /v1/recommendations[/recompute]
│   │   ├── flights.py                   # GET  /v1/flights/{number}/{date}, /v1/flights/search
│   │   ├── auth.py                      # POST /v1/auth/{send-otp,verify-otp,social}
│   │   ├── users.py                     # GET  /v1/users/me, PUT /v1/users/preferences
│   │   └── events.py                    # POST /v1/events
│   └── middleware/
│       └── auth.py                      # JWT token decoding + dependency injection
├── schemas/                             # Pydantic v2 request/response models
├── services/
│   ├── trip_intake.py                   # Trip validation, normalization, persistence
│   ├── recommendation_service.py        # Core engine — segment builder + confidence profiles
│   ├── flight_snapshot_service.py       # Flight data aggregation with fallbacks
│   ├── trial.py                         # Free tier logic (3-trip trial)
│   └── integrations/
│       ├── aerodatabox.py               # Live flight status (RapidAPI)
│       ├── google_maps.py               # Traffic-aware drive time (Distance Matrix)
│       ├── tsa_model.py                 # TSA wait estimation (percentile model)
│       ├── airport_graph.py             # Terminal walking time (graph-based shortest path)
│       └── airport_defaults.py          # Flat timing defaults per airport
├── db/
│   ├── __init__.py                      # Async engine + session factory
│   └── models.py                        # ORM: User, Trip, Recommendation, Event, etc.
└── data/
    ├── tsa_baselines.json               # TSA percentiles by airport/day/hour
    └── airports/                        # Per-airport graph configs (SFO, OAK, SJC, LAX, JFK, ...)
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL (or a [Supabase](https://supabase.com) project)
- API keys: [RapidAPI](https://rapidapi.com/) (AeroDataBox), [Google Maps Platform](https://developers.google.com/maps)

### Setup

```bash
# Clone
git clone https://github.com/rabahbabaci/airbridge-backend.git
cd airbridge-backend

# Virtual environment
python -m venv .venv
source .venv/bin/activate

# Dependencies
pip install -r requirements.txt

# Environment config
cp .env.example .env   # then fill in your keys

# Run database migrations
alembic upgrade head

# Start dev server
PYTHONPATH=src uvicorn app.main:app --reload --port 8000
```

The API is now live at:

| | URL |
|-|-----|
| **Root** | http://localhost:8000 |
| **Interactive docs** | http://localhost:8000/docs |
| **OpenAPI spec** | http://localhost:8000/openapi.json |

---

## API Reference

### Health & Metadata

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/version` | App name, version, environment |

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/auth/send-otp` | Send phone OTP via Supabase |
| `POST` | `/v1/auth/verify-otp` | Verify OTP, receive JWT (30-day expiry) |
| `POST` | `/v1/auth/social` | Sign in with Apple or Google |

### Trips

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/v1/trips` | Optional | Create trip context (flight number or route search mode) |

Supports two input modes via a [discriminated union](https://docs.pydantic.dev/latest/concepts/unions/#discriminated-unions) on `input_mode`:

<details>
<summary><strong>Flight number mode</strong> — user knows their flight</summary>

```json
{
  "input_mode": "flight_number",
  "flight_number": "UA452",
  "departure_date": "2026-06-01",
  "home_address": "742 Evergreen Terrace, Berkeley, CA 94701",
  "preferences": {
    "transport_mode": "rideshare",
    "confidence_profile": "sweet",
    "security_access": "precheck",
    "bag_count": 1,
    "has_boarding_pass": true
  }
}
```
</details>

<details>
<summary><strong>Route search mode</strong> — user needs to find their flight</summary>

```json
{
  "input_mode": "route_search",
  "airline": "Southwest",
  "origin_airport": "SFO",
  "destination_airport": "AUS",
  "departure_date": "2026-06-01",
  "departure_time_window": "afternoon",
  "home_address": "742 Evergreen Terrace, Berkeley, CA 94701",
  "preferences": {
    "transport_mode": "driving",
    "confidence_profile": "safety",
    "bag_count": 2,
    "traveling_with_children": true
  }
}
```
</details>

### Recommendations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/v1/recommendations` | Optional | Compute leave-home recommendation |
| `POST` | `/v1/recommendations/recompute` | Optional | Recompute with preference overrides |

Returns a segment-by-segment breakdown:

```
transport (32 min) -> parking (12 min) -> bag_drop (5 min) -> tsa (8 min) -> walk_to_gate (10 min) -> gate_buffer (15 min)
```

Each segment includes `id`, `label`, `duration_minutes`, and contextual `advice`.

### Flights

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/flights/{flight_number}/{date}` | Lookup flight by number and date |
| `GET` | `/v1/flights/search` | Search departures by route, time window, airline |

### Users

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/v1/users/me` | Required | Current user profile + tier info |
| `PUT` | `/v1/users/preferences` | Required | Update saved travel preferences |

### Events

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/v1/events` | Optional | Record analytics event |

### Preference Fields

| Field | Options | Default | Description |
|-------|---------|---------|-------------|
| `transport_mode` | `rideshare` `driving` `train` `bus` `other` | `driving` | How you get to the airport |
| `confidence_profile` | `safety` `sweet` `risk` | `sweet` | Time vs. certainty tradeoff |
| `security_access` | `none` `precheck` `clear` `clear_precheck` `priority_lane` | `none` | Expedited security program |
| `bag_count` | `0`–`10` | `0` | Number of checked bags |
| `traveling_with_children` | `boolean` | `false` | Adds 1.4x walking time multiplier |
| `has_boarding_pass` | `boolean` | `true` | Skip check-in counter if true |
| `extra_time_minutes` | `0` `15` `30` | `0` | Manual buffer time |
| `gate_time_minutes` | `0`–`180` | Profile default | Custom gate buffer override |

### Error Format

All errors return a consistent JSON shape:

```json
{
  "code": "INVALID_INPUT",
  "message": "Human-readable description.",
  "details": [...]
}
```

---

## Database

PostgreSQL with async SQLAlchemy 2.0. Migrations managed by Alembic.

### Models

| Table | Purpose |
|-------|---------|
| `users` | Profile, auth provider, subscription status, saved preferences |
| `trips` | Trip context (input mode, flight, address, preferences JSON) |
| `recommendations` | Computed recommendation + segments JSON |
| `device_tokens` | Push notification tokens (iOS/Android) |
| `feedback` | Post-trip accuracy feedback (for model calibration) |
| `events` | Analytics events with optional metadata |

### Migrations

```bash
alembic upgrade head     # Apply all pending migrations
alembic downgrade -1     # Revert the last migration
```

### Tier System

| Tier | Access |
|------|--------|
| **Pro** | First 3 trips free, or active subscription |
| **Free** | Limited feature set after trial expires |

---

## Testing

```bash
# Run all tests
PYTHONPATH=src pytest tests/ -v

# Run a specific test file
PYTHONPATH=src pytest tests/test_recommendations.py -v

# Run a single test
PYTHONPATH=src pytest tests/test_trips.py::TestFlightNumberMode::test_returns_201 -v
```

Test suite covers: trip intake, recommendations, flight search, auth (OTP + social), JWT middleware, user profiles, preferences, analytics events, trial tier logic, and parking segments.

---

## Airport Coverage

AirBridge ships with timing profiles and graph-based terminal models for **10 airports**:

| Airport | Graph Model | TSA Baselines |
|---------|:-----------:|:-------------:|
| SFO — San Francisco | Yes | Yes |
| OAK — Oakland | Yes | Yes |
| SJC — San Jose | Yes | Yes |
| LAX — Los Angeles | Yes | Yes |
| JFK — New York JFK | Yes | Yes |
| ORD — Chicago O'Hare | Yes | Yes |
| ATL — Atlanta | Yes | Yes |
| DFW — Dallas/Fort Worth | Yes | Yes |
| SEA — Seattle-Tacoma | Yes | Yes |
| BOS — Boston Logan | Yes | Yes |

Adding a new airport requires only a JSON config file in `src/app/data/airports/` — no code changes.

---

## Environment Variables

```env
# Application
APP_NAME=airbridge-backend
APP_ENV=development          # development | production
APP_PORT=8000

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname

# Auth (Supabase)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-publishable-key
JWT_SECRET=your-signing-secret

# External APIs
RAPIDAPI_KEY=your-key         # AeroDataBox flight data
GOOGLE_MAPS_API_KEY=your-key  # Distance Matrix API

# Monitoring
SENTRY_DSN=https://...@ingest.sentry.io/...
```

---

## Related Repos

| Repo | Description |
|------|-------------|
| [airbridge-frontend](https://github.com/rabahbabaci/airbridge-frontend) | Mobile/web client |

---

## License

[MIT](LICENSE)
