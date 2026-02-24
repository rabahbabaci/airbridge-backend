# AirBridge Backend

Production backend for **AirBridge** — the door-to-gate departure decision engine.

---

## Purpose

This backend powers the full AirBridge app lifecycle:

1. Intake user trip inputs (dual mode: flight number or route search)
2. Resolve transport + airport + flight context
3. Compute leave-home recommendation with confidence
4. Recompute on live changes (traffic/flight/TSA/gate)
5. Notify user when recommendation changes materially

---

## Stack

- **Python 3.11+**
- **FastAPI** — API framework
- **Pydantic v2** — request/response validation
- **Uvicorn** — ASGI server
- **pytest + httpx** — testing

---

## Project Structure

```
airbridge-backend/
├── pyproject.toml
├── requirements.txt
├── .env.example
├── src/
│   └── app/
│       ├── main.py                        # FastAPI app + router registration
│       ├── core/
│       │   ├── config.py                  # App settings from environment
│       │   └── errors.py                  # Error classes + exception handlers
│       ├── api/
│       │   └── routes/
│       │       ├── health.py              # GET /health
│       │       ├── version.py             # GET /version
│       │       ├── trips.py               # POST /v1/trips
│       │       └── recommendations.py     # POST /v1/recommendations[/recompute]
│       ├── schemas/
│       │   ├── trips.py                   # Dual-mode trip request + TripContext
│       │   ├── recommendations.py
│       │   └── health.py
│       └── services/
│           ├── trip_intake.py             # Trip normalization logic
│           └── recommendation_service.py
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_version.py
│   ├── test_trips.py
│   └── test_recommendations.py
└── docs/
```

---

## Run Locally

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy environment config
cp .env.example .env

# 4. Start the dev server
PYTHONPATH=src uvicorn app.main:app --reload --port 8000

# API available at:   http://localhost:8000
# Interactive docs:   http://localhost:8000/docs
# OpenAPI schema:     http://localhost:8000/openapi.json
```

---

## Run Tests

```bash
PYTHONPATH=src pytest tests/ -v
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/version` | App name, version, and environment |
| `POST` | `/v1/trips` | Intake and validate a trip context (dual mode) |
| `POST` | `/v1/recommendations` | Compute leave-home recommendation |
| `POST` | `/v1/recommendations/recompute` | Recompute recommendation for existing trip |

---

## Sample curl Requests

### GET /health

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### GET /version

```bash
curl http://localhost:8000/version
# {"app_name":"airbridge-backend","version":"0.1.0","environment":"development"}
```

### POST /v1/trips — flight_number mode

```bash
curl -X POST http://localhost:8000/v1/trips \
  -H "Content-Type: application/json" \
  -d '{
    "input_mode": "flight_number",
    "flight_number": "AA123",
    "departure_date": "2026-06-01",
    "home_address": "123 Main St, New York, NY 10001"
  }'
```

Expected response (201):
```json
{
  "trip_id": "<uuid>",
  "input_mode": "flight_number",
  "flight_number": "AA123",
  "departure_date": "2026-06-01",
  "home_address": "123 Main St, New York, NY 10001",
  "created_at": "<iso-timestamp>",
  "status": "validated"
}
```

### POST /v1/trips — route_search mode

```bash
curl -X POST http://localhost:8000/v1/trips \
  -H "Content-Type: application/json" \
  -d '{
    "input_mode": "route_search",
    "airline": "American Airlines",
    "origin_airport": "JFK",
    "destination_airport": "LAX",
    "departure_date": "2026-06-01",
    "departure_time_window": "morning",
    "home_address": "123 Main St, New York, NY 10001"
  }'
```

Expected response (201):
```json
{
  "trip_id": "<uuid>",
  "input_mode": "route_search",
  "airline": "American Airlines",
  "origin_airport": "JFK",
  "destination_airport": "LAX",
  "departure_date": "2026-06-01",
  "departure_time_window": "morning",
  "home_address": "123 Main St, New York, NY 10001",
  "created_at": "<iso-timestamp>",
  "status": "validated"
}
```

---

## Error Contract

All errors return a consistent JSON shape:

```json
{
  "code": "INVALID_INPUT",
  "message": "Human-readable description.",
  "details": [...]
}
```

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_INPUT` | 422 | Missing or invalid request fields |
| `UNSUPPORTED_MODE` | 422 | `input_mode` value not recognized |

---

## Cursor Smooth Start

1. Open folder in Cursor
2. Read `CURSOR_SETUP.md`
3. Follow `docs/product/requirements.md`
4. Implement from `docs/api/contracts.md`
