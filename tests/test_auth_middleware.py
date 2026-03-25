from fastapi.testclient import TestClient

TRIP_PAYLOAD = {
    "input_mode": "flight_number",
    "flight_number": "AA123",
    "departure_date": "2026-06-01",
    "home_address": "123 Main St, New York, NY 10001",
}


def test_trips_without_auth_still_works(client: TestClient) -> None:
    response = client.post("/v1/trips", json=TRIP_PAYLOAD)
    assert response.status_code == 201
    assert "trip_id" in response.json()


def test_trips_with_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/v1/trips",
        json=TRIP_PAYLOAD,
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert response.status_code == 201
    assert "trip_id" in response.json()


def test_trips_with_malformed_header(client: TestClient) -> None:
    response = client.post(
        "/v1/trips",
        json=TRIP_PAYLOAD,
        headers={"Authorization": "Token xyz"},
    )
    assert response.status_code == 201
    assert "trip_id" in response.json()
