from fastapi.testclient import TestClient

TRIP_ID = "test-trip-abc-123"


def test_compute_recommendation_returns_200(client: TestClient) -> None:
    response = client.post("/v1/recommendations", json={"trip_id": TRIP_ID})
    assert response.status_code == 200


def test_compute_recommendation_response_shape(client: TestClient) -> None:
    response = client.post("/v1/recommendations", json={"trip_id": TRIP_ID})
    body = response.json()
    assert body["trip_id"] == TRIP_ID
    assert "leave_home_at" in body
    assert "confidence" in body
    assert "confidence_score" in body
    assert 0.0 <= body["confidence_score"] <= 1.0
    assert "explanation" in body
    assert isinstance(body["segments"], list)
    assert "computed_at" in body


def test_compute_recommendation_missing_trip_id_returns_422(client: TestClient) -> None:
    response = client.post("/v1/recommendations", json={})
    assert response.status_code == 422


def test_recompute_recommendation_returns_200(client: TestClient) -> None:
    response = client.post("/v1/recommendations/recompute", json={"trip_id": TRIP_ID})
    assert response.status_code == 200


def test_recompute_recommendation_response_shape(client: TestClient) -> None:
    response = client.post("/v1/recommendations/recompute", json={"trip_id": TRIP_ID})
    body = response.json()
    assert body["trip_id"] == TRIP_ID
    assert "leave_home_at" in body
    assert "confidence_score" in body


def test_recompute_with_reason_reflects_in_explanation(client: TestClient) -> None:
    payload = {"trip_id": TRIP_ID, "reason": "traffic_update"}
    response = client.post("/v1/recommendations/recompute", json=payload)
    body = response.json()
    assert "traffic_update" in body["explanation"]


def test_recompute_without_reason_is_valid(client: TestClient) -> None:
    response = client.post("/v1/recommendations/recompute", json={"trip_id": TRIP_ID})
    assert response.status_code == 200


def test_recompute_missing_trip_id_returns_422(client: TestClient) -> None:
    response = client.post("/v1/recommendations/recompute", json={})
    assert response.status_code == 422
