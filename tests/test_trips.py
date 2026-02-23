import pytest
from fastapi.testclient import TestClient

VALID_TRIP_PAYLOAD = {
    "origin_address": "123 Main St, New York, NY 10001",
    "airport_code": "JFK",
    "flight_number": "AA123",
    "departure_time": "2026-06-01T14:00:00Z",
    "bag_count": 1,
    "children_count": 0,
    "transport_mode": "driving",
}


def test_create_trip_returns_201(client: TestClient) -> None:
    response = client.post("/v1/trips", json=VALID_TRIP_PAYLOAD)
    assert response.status_code == 201


def test_create_trip_response_shape(client: TestClient) -> None:
    response = client.post("/v1/trips", json=VALID_TRIP_PAYLOAD)
    body = response.json()
    assert "trip_id" in body
    assert body["status"] == "created"
    assert body["airport_code"] == "JFK"
    assert body["flight_number"] == "AA123"
    assert body["bag_count"] == 1
    assert body["transport_mode"] == "driving"


def test_create_trip_generates_unique_ids(client: TestClient) -> None:
    r1 = client.post("/v1/trips", json=VALID_TRIP_PAYLOAD)
    r2 = client.post("/v1/trips", json=VALID_TRIP_PAYLOAD)
    assert r1.json()["trip_id"] != r2.json()["trip_id"]


def test_create_trip_missing_required_field_returns_422(client: TestClient) -> None:
    payload = {k: v for k, v in VALID_TRIP_PAYLOAD.items() if k != "flight_number"}
    response = client.post("/v1/trips", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("transport_mode", ["driving", "transit", "rideshare", "walking"])
def test_create_trip_all_transport_modes(client: TestClient, transport_mode: str) -> None:
    payload = {**VALID_TRIP_PAYLOAD, "transport_mode": transport_mode}
    response = client.post("/v1/trips", json=payload)
    assert response.status_code == 201
    assert response.json()["transport_mode"] == transport_mode
