from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.services.trial import is_pro, get_tier_info

TRIP_PAYLOAD = {
    "input_mode": "flight_number",
    "flight_number": "AA123",
    "departure_date": "2026-06-01",
    "home_address": "123 Main St, New York, NY 10001",
}


def _create_trip(client: TestClient) -> str:
    r = client.post("/v1/trips", json=TRIP_PAYLOAD)
    assert r.status_code == 201
    return r.json()["trip_id"]


def test_recommendation_has_tier_field(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations", json={"trip_id": trip_id})
    assert response.status_code == 200
    assert response.json()["tier"] == "free"


def test_recommendation_has_remaining_pro_trips_field(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations", json={"trip_id": trip_id})
    assert response.status_code == 200
    assert response.json()["remaining_pro_trips"] is None


def test_is_pro_none_user() -> None:
    assert is_pro(None) is False


def test_is_pro_new_user() -> None:
    user = SimpleNamespace(trip_count=1, subscription_status="none")
    assert is_pro(user) is True


def test_is_pro_exhausted_user() -> None:
    user = SimpleNamespace(trip_count=5, subscription_status="none")
    assert is_pro(user) is False


def test_is_pro_subscribed_user() -> None:
    user = SimpleNamespace(trip_count=10, subscription_status="active")
    assert is_pro(user) is True
