# API Contracts (Draft v0)

## POST /v1/trips

Create/validate a trip context. Two input modes: `flight_number` or `route_search`.

### Request (discriminated by `input_mode`)

**flight_number**
```json
{
  "input_mode": "flight_number",
  "flight_number": "AA123",
  "departure_date": "2026-06-01",
  "home_address": "123 Main St, New York, NY 10001",
  "transport_mode": "driving",
  "confidence_profile": "sweet",
  "bag_count": 0,
  "traveling_with_children": false,
  "extra_time_minutes": 0
}
```

**route_search** (requires `departure_time_window`)
```json
{
  "input_mode": "route_search",
  "airline": "American Airlines",
  "origin_airport": "JFK",
  "destination_airport": "LAX",
  "departure_date": "2026-06-01",
  "departure_time_window": "morning",
  "home_address": "123 Main St, New York, NY 10001",
  "transport_mode": "driving",
  "confidence_profile": "sweet",
  "bag_count": 0,
  "traveling_with_children": false,
  "extra_time_minutes": 0
}
```

- `transport_mode`: enum `rideshare` | `driving` | `train` | `bus` | `other` (default `driving`)
- `confidence_profile`: enum `safety` | `sweet` | `risk` (default `sweet`)
- `bag_count`: int 0–3 (default 0)
- `traveling_with_children`: bool (default false)
- `extra_time_minutes`: 0 | 15 | 30 (default 0)
- `departure_time_window`: required for route_search only; enum `morning` | `midday` | `afternoon` | `evening` | `late_night` | `not_sure`

### Response (201)

```json
{
  "trip_id": "uuid",
  "input_mode": "flight_number",
  "departure_date": "2026-06-01",
  "home_address": "123 Main St, New York, NY 10001",
  "created_at": "2026-02-26T12:00:00Z",
  "status": "validated",
  "transport_mode": "driving",
  "confidence_profile": "sweet",
  "bag_count": 0,
  "traveling_with_children": false,
  "extra_time_minutes": 0,
  "flight_number": "AA123",
  "airline": null,
  "origin_airport": null,
  "destination_airport": null,
  "departure_time_window": null
}
```

---

## POST /v1/recommendations

Compute leave-home recommendation for a trip. Trip must have been created via POST /v1/trips first.

### Request

```json
{
  "trip_id": "uuid-from-post-trips"
}
```

### Response (200)

```json
{
  "trip_id": "uuid",
  "leave_home_at": "2026-06-01T07:30:00Z",
  "confidence": "medium",
  "confidence_score": 0.85,
  "explanation": "Base lead 90 min + airport baseline (40+30 min) + driving offset, sweet profile. +14 min for 2 bag(s), +10 min for kids.",
  "segments": [
    { "id": "home_buffer", "label": "Home buffer", "duration_minutes": 90, "advice": "Leave home in time for transport." },
    { "id": "transport", "label": "Transport to airport", "duration_minutes": 10, "advice": "Allow time for driving." },
    { "id": "check_in_security", "label": "Check-in & security", "duration_minutes": 70, "advice": "TSA and check-in buffer." },
    { "id": "gate_buffer", "label": "Gate buffer", "duration_minutes": 15, "advice": "Reach gate before boarding." },
    { "id": "extra_buffer", "label": "Extra buffer", "duration_minutes": 24, "advice": "Bags, children, and extra time." }
  ],
  "computed_at": "2026-02-26T12:00:00Z"
}
```

- `confidence`: `high` | `medium` | `low`
- `segments`: list of `{ id, label, duration_minutes, advice }`

Returns **404** if `trip_id` is not found.

---

## POST /v1/recommendations/recompute

Recompute recommendation for an existing trip. Optionally pass `preference_overrides` (same shape as trip preferences); when provided, overrides are used instead of stored trip preferences.

### Request

```json
{
  "trip_id": "uuid",
  "reason": "traffic_update",
  "preference_overrides": {
    "transport_mode": "rideshare",
    "confidence_profile": "safety",
    "bag_count": 2,
    "traveling_with_children": true,
    "extra_time_minutes": 15
  }
}
```

- `reason`: optional string (e.g. `traffic_update`)
- `preference_overrides`: optional; any subset of `transport_mode`, `confidence_profile`, `bag_count`, `traveling_with_children`, `extra_time_minutes`

### Response (200)

Same shape as POST /v1/recommendations. If `reason` is provided, it is prepended to `explanation`.

Returns **404** if `trip_id` is not found.
