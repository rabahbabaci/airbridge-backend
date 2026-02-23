# AirBridge Backend

Production backend for **AirBridge** — the door-to-gate departure decision engine.

---

## Purpose

This backend powers the full AirBridge app lifecycle:

1. Intake user trip inputs
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
├── src/
│   └── app/
│       ├── main.py                        # FastAPI app + router registration
│       ├── api/
│       │   └── routes/
│       │       ├── health.py              # GET /health
│       │       ├── trips.py               # POST /v1/trips
│       │       └── recommendations.py     # POST /v1/recommendations[/recompute]
│       ├── schemas/
│       │   ├── trips.py
│       │   ├── recommendations.py
│       │   └── health.py
│       └── services/
│           ├── trip_service.py
│           └── recommendation_service.py
├── tests/
│   ├── conftest.py
│   ├── test_health.py
│   ├── test_trips.py
│   └── test_recommendations.py
└── docs/
```

---

## Run Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the dev server
PYTHONPATH=src uvicorn app.main:app --reload

# API available at http://localhost:8000
# Interactive docs at http://localhost:8000/docs
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
| `POST` | `/v1/trips` | Create/validate a trip context |
| `POST` | `/v1/recommendations` | Compute leave-home recommendation |
| `POST` | `/v1/recommendations/recompute` | Recompute recommendation for existing trip |

---

## Cursor Smooth Start

1. Open folder in Cursor
2. Read `CURSOR_SETUP.md`
3. Follow `docs/product/requirements.md`
4. Implement from `docs/api/contracts.md`
