# ✈️ AirBridge — Backend API

**The door-to-gate departure decision engine.**

AirBridge eliminates the guesswork of airport timing. It computes a personalized "leave home by" recommendation powered by real-time flight data, live TSA wait times, traffic conditions, and airport-specific walking models — so you never wait, never rush, and just board.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](LICENSE)

---

## Overview

This repository contains the production API backend for AirBridge. It handles:

- **Trip intake** — Accepts user trip details in two modes: direct flight number lookup or route-based search with time window filtering
- **Recommendation engine** — Computes a confidence-scored, segment-by-segment departure timeline (home → transport → airport → baggage → security → gate)
- **Preference-aware** — Factors in transport mode, checked bags, children, security access (PreCheck/CLEAR), risk profile, and extra buffer time
- **Live recompute** — Recalculates recommendations when conditions change (traffic, flight delays, TSA surges) *(Week 2)*
- **Airport profiles** — Per-airport timing data for SFO, OAK, and SJC with extensible architecture for nationwide expansion

> **Schema contract:** Any change to `src/app/schemas/*.py` must be mirrored in `airbridge-frontend/src/api/airbridge.contracts.ts` to keep the frontend types in sync.

---

## Architecture

```
Client (Base44 frontend)
  │
  ▼
FastAPI (this repo)
  ├── POST /v1/trips              → validate + store trip context
  ├── POST /v1/recommendations    → compute leave-home recommendation
  └── POST /v1/recommendations/recompute → recompute with overrides
          │
          ▼
    ┌─────────────────────────────────────┐
    │       Recommendation Engine         │
    │  ┌──────────┐  ┌────────────────┐   │
    │  │ Airport   │  │ Flight         │   │
    │  │ Profiles  │  │ Snapshot       │   │
    │  └──────────┘  └────────────────┘   │
    │  ┌──────────┐  ┌────────────────┐   │
    │  │ Formula   │  │ Segment        │   │
    │  │ Engine    │  │ Builder        │   │
    │  └──────────┘  └────────────────┘   │
    └─────────────────────────────────────┘
          │
          ▼ (Week 2)
    External Providers
    ├── Flight status API (Aviationstack / FlightAware)
    ├── TSA wait times API
    └── Google Routes API (traffic-aware travel time)
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI |
| Validation | Pydantic v2 |
| Server | Uvicorn (ASGI) |
| Language | Python 3.11+ |
| Testing | pytest + httpx |

---

## Project Structure

```
src/app/
├── main.py                          # App entry point, CORS, router registration
├── core/
│   ├── config.py                    # Environment-driven settings
│   └── errors.py                    # Structured error classes + handlers
├── api/routes/
│   ├── health.py                    # GET /health
│   ├── version.py                   # GET /version
│   ├── trips.py                     # POST /v1/trips
│   └── recommendations.py          # POST /v1/recommendations[/recompute]
├── schemas/
│   ├── trips.py                     # TripRequest, TripContext, TripPreferences
│   ├── recommendations.py          # RecommendationRequest/Response, SegmentDetail
│   ├── flight_snapshot.py          # FlightSnapshot, AirportTimings
│   └── health.py                    # HealthResponse
└── services/
    ├── trip_intake.py               # Trip validation, normalization, in-memory store
    ├── recommendation_service.py   # Formula engine + segment builder
    └── flight_snapshot_service.py  # Airport profiles + flight data (fallback-ready)
```

---

## Getting Started

```bash
# Clone the repository
git clone https://github.com/rabahbabaci/airbridge-backend.git
cd airbridge-backend

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env

# Start the development server
PYTHONPATH=src uvicorn app.main:app --reload --port 8000
```

API surfaces:

- **API root:** <http://localhost:8000>
- **Interactive docs:** <http://localhost:8000/docs>
- **OpenAPI schema:** <http://localhost:8000/openapi.json>

---

## Running Tests

```bash
PYTHONPATH=src pytest tests/ -v
```

---

## API Reference

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/version` | App name, version, environment |
| `POST` | `/v1/trips` | Create and validate a trip context |
| `POST` | `/v1/recommendations` | Compute leave-home recommendation |
| `POST` | `/v1/recommendations/recompute` | Recompute with preference overrides |

### Trip Input Modes

**Flight number mode** — When the user knows their flight:

```bash
curl -X POST http://localhost:8000/v1/trips \
  -H "Content-Type: application/json" \
  -d '{
    "input_mode": "flight_number",
    "flight_number": "UA452",
    "departure_date": "2026-06-01",
    "home_address": "742 Evergreen Terrace, Berkeley, CA 94701",
    "preferences": {
      "transport_mode": "rideshare",
      "confidence_profile": "sweet",
      "bag_count": 1,
      "has_boarding_pass": true,
      "security_access": "precheck"
    }
  }'
```

**Route search mode** — When the user needs to find their flight:

```bash
curl -X POST http://localhost:8000/v1/trips \
  -H "Content-Type: application/json" \
  -d '{
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
      "traveling_with_children": true,
      "security_access": "none"
    }
  }'
```

### Preference Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `transport_mode` | `rideshare` · `driving` · `train` · `bus` · `other` | `driving` | How you get to the airport |
| `confidence_profile` | `safety` · `sweet` · `risk` | `sweet` | Time vs. certainty trade-off |
| `bag_count` | `0–3` | `0` | Number of checked bags |
| `traveling_with_children` | `boolean` | `false` | Traveling with kids |
| `extra_time_minutes` | `0` · `15` · `30` | `0` | Manual buffer |
| `has_boarding_pass` | `boolean` | `true` | Already have boarding pass |
| `security_access` | `none` · `precheck` · `clear` · `clear_precheck` · `priority_lane` | `none` | Expedited security program |

### Error Format

All errors return a consistent shape:

```json
{
  "code": "INVALID_INPUT",
  "message": "Human-readable description.",
  "details": [...]
}
```

---

## Beta Scope

- **Airports:** SFO, OAK, SJC (Bay Area)
- **Architecture:** Airport-agnostic — expanding to new airports requires adding profile data, not code changes

---

## Roadmap

- [x] Trip intake (dual mode) + validation
- [x] Preference system (transport, bags, security access, risk profile)
- [x] Recommendation engine with segment breakdown
- [x] Per-airport timing profiles (SFO, OAK, SJC)
- [x] Fallback-ready provider architecture
- [ ] Live flight status integration
- [ ] Real-time TSA wait times
- [ ] Traffic-aware transport timing (Google Routes)
- [ ] Push notifications on recommendation changes
- [ ] Airport walking model (curb → check-in → security → gate)
- [ ] Frontend ↔ backend integration

---

## Related

- **Frontend:** [airbridge-frontend](https://github.com/rabahbabaci/airbridge-frontend)
- **Live beta:** [airbridgeberkeley.base44.app](https://airbridgeberkeley.base44.app)

---

## License

[MIT](LICENSE)
