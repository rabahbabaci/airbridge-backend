import pytest
from fastapi.testclient import TestClient

FLIGHT_NUMBER_PAYLOAD = {
    "input_mode": "flight_number",
    "flight_number": "AA123",
    "departure_date": "2026-06-01",
    "home_address": "123 Main St, New York, NY 10001",
}

ROUTE_SEARCH_PAYLOAD = {
    "input_mode": "route_search",
    "airline": "American Airlines",
    "origin_airport": "JFK",
    "destination_airport": "LAX",
    "departure_date": "2026-06-01",
    "departure_time_window": "morning",
    "home_address": "123 Main St, New York, NY 10001",
}


class TestFlightNumberMode:
    def test_returns_201(self, client: TestClient) -> None:
        response = client.post("/v1/trips", json=FLIGHT_NUMBER_PAYLOAD)
        assert response.status_code == 201

    def test_response_shape(self, client: TestClient) -> None:
        body = client.post("/v1/trips", json=FLIGHT_NUMBER_PAYLOAD).json()
        assert "trip_id" in body
        assert body["input_mode"] == "flight_number"
        assert body["flight_number"] == "AA123"
        assert body["departure_date"] == "2026-06-01"
        assert body["home_address"] == "123 Main St, New York, NY 10001"
        assert body["status"] == "validated"
        assert "created_at" in body

    def test_generates_unique_trip_ids(self, client: TestClient) -> None:
        r1 = client.post("/v1/trips", json=FLIGHT_NUMBER_PAYLOAD).json()
        r2 = client.post("/v1/trips", json=FLIGHT_NUMBER_PAYLOAD).json()
        assert r1["trip_id"] != r2["trip_id"]

    def test_normalizes_flight_number_to_uppercase(self, client: TestClient) -> None:
        payload = {**FLIGHT_NUMBER_PAYLOAD, "flight_number": "  aa123  "}
        body = client.post("/v1/trips", json=payload).json()
        assert body["flight_number"] == "AA123"

    def test_missing_flight_number_returns_422(self, client: TestClient) -> None:
        payload = {k: v for k, v in FLIGHT_NUMBER_PAYLOAD.items() if k != "flight_number"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    def test_missing_departure_date_returns_422(self, client: TestClient) -> None:
        payload = {k: v for k, v in FLIGHT_NUMBER_PAYLOAD.items() if k != "departure_date"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    def test_missing_home_address_returns_422(self, client: TestClient) -> None:
        payload = {k: v for k, v in FLIGHT_NUMBER_PAYLOAD.items() if k != "home_address"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422


class TestRouteSearchMode:
    def test_returns_201(self, client: TestClient) -> None:
        response = client.post("/v1/trips", json=ROUTE_SEARCH_PAYLOAD)
        assert response.status_code == 201

    def test_response_shape(self, client: TestClient) -> None:
        body = client.post("/v1/trips", json=ROUTE_SEARCH_PAYLOAD).json()
        assert "trip_id" in body
        assert body["input_mode"] == "route_search"
        assert body["airline"] == "American Airlines"
        assert body["origin_airport"] == "JFK"
        assert body["destination_airport"] == "LAX"
        assert body["departure_date"] == "2026-06-01"
        assert body["departure_time_window"] == "morning"
        assert body["home_address"] == "123 Main St, New York, NY 10001"
        assert body["status"] == "validated"
        assert "created_at" in body

    def test_normalizes_airport_codes_to_uppercase(self, client: TestClient) -> None:
        payload = {**ROUTE_SEARCH_PAYLOAD, "origin_airport": "jfk", "destination_airport": "lax"}
        body = client.post("/v1/trips", json=payload).json()
        assert body["origin_airport"] == "JFK"
        assert body["destination_airport"] == "LAX"

    def test_invalid_airport_code_length_returns_422(self, client: TestClient) -> None:
        payload = {**ROUTE_SEARCH_PAYLOAD, "origin_airport": "JFKX"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    def test_same_origin_destination_returns_422(self, client: TestClient) -> None:
        payload = {**ROUTE_SEARCH_PAYLOAD, "origin_airport": "JFK", "destination_airport": "JFK"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    def test_invalid_time_window_returns_422(self, client: TestClient) -> None:
        payload = {**ROUTE_SEARCH_PAYLOAD, "departure_time_window": "midnight"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "window",
        ["morning", "midday", "afternoon", "evening", "late_night", "not_sure"],
    )
    def test_all_time_windows_accepted(self, client: TestClient, window: str) -> None:
        payload = {**ROUTE_SEARCH_PAYLOAD, "departure_time_window": window}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 201
        assert response.json()["departure_time_window"] == window

    def test_missing_airline_returns_422(self, client: TestClient) -> None:
        payload = {k: v for k, v in ROUTE_SEARCH_PAYLOAD.items() if k != "airline"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422


class TestUnsupportedMode:
    def test_unsupported_mode_returns_422(self, client: TestClient) -> None:
        payload = {"input_mode": "magic_mode", "home_address": "123 Main St"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    def test_missing_input_mode_returns_422(self, client: TestClient) -> None:
        payload = {"flight_number": "AA123", "departure_date": "2026-06-01", "home_address": "123 Main St"}
        response = client.post("/v1/trips", json=payload)
        assert response.status_code == 422

    def test_error_body_has_code_and_message(self, client: TestClient) -> None:
        payload = {"input_mode": "bad_mode"}
        body = client.post("/v1/trips", json=payload).json()
        assert "code" in body
        assert "message" in body
