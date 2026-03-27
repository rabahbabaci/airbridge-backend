# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Airbridge is a door-to-gate departure decision engine for airport trips. It computes personalized leave-home times by combining live flight data, traffic estimates, TSA wait models, and airport walking times into a segment-by-segment timeline. Python 3.11+ / FastAPI / Pydantic v2. Currently MVP stage targeting Bay Area airports (SFO, OAK, SJC).

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
```

No linter or formatter is configured in the project yet.

## Architecture

**Request flow**: Route handler → Schema validation (Pydantic) → Service layer → Integration clients → Response model

### Key layers

- **`src/app/api/routes/`** — FastAPI routers. `trips.py` handles trip intake (POST /v1/trips). `recommendations.py` handles recommendation compute and recompute.
- **`src/app/schemas/`** — Pydantic v2 models for request/response validation. Trip input uses a discriminated union on `input_mode` field (flight_number vs route_search).
- **`src/app/services/`** — Business logic. `trip_intake.py` validates/normalizes and stores trips in-memory (FIFO, max 1000). `recommendation_service.py` is the core engine that builds a segment timeline (transport → airport → baggage → security → gate) with three confidence profiles (safety: 0.92, sweet: 0.85, risk: 0.70).
- **`src/app/services/integrations/`** — External API clients. AeroDataBox for flight status, Google Maps for travel time, TSA model for security wait estimates, airport graph/defaults for walking times. All integrations have deterministic fallbacks when APIs are unavailable.
- **`src/app/data/airports/`** — JSON profiles per airport. Adding a new airport requires only a new JSON file, no code changes.
- **`src/app/core/`** — `config.py` loads settings from env vars; `errors.py` defines `AppError` hierarchy with structured JSON responses.

### Important patterns

- **Preference overrides**: `_effective_context()` in recommendation_service merges user preference overrides (security_access, has_boarding_pass, etc.) into the trip context for recompute.
- **In-memory stores**: Both trip store and flight cache use dict-based FIFO eviction (no database yet).
- **Pydantic validation**: Field validators normalize input (trim whitespace, uppercase IATA codes). Model validators handle cross-field rules.
- **Error shape**: All errors return `{"code": "...", "message": "...", "details": [...]}`.

## Testing

Tests use pytest with `asyncio_mode = "auto"`. Test client is FastAPI's TestClient (via httpx). Tests are organized by endpoint in class-based groups. `PYTHONPATH=src` is required for all test/run commands.

## Environment Variables

Required in `.env`: `RAPIDAPI_KEY` (AeroDataBox), `GOOGLE_MAPS_API_KEY`. See `.env` for full list.

## Database Backup Protocol

Before running any Alembic migration against production, take a backup:
```bash
pg_dump $DATABASE_URL > backup_$(date +%Y%m%d).sql
```

Run this from your local machine or Railway shell. Verify the backup file is non-empty before proceeding with the migration.
