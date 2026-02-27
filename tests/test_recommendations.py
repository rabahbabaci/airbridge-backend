from fastapi.testclient import TestClient

TRIP_PAYLOAD = {
    "input_mode": "flight_number",
    "flight_number": "AA123",
    "departure_date": "2026-06-01",
    "home_address": "123 Main St, New York, NY 10001",
}


def _create_trip(client: TestClient, **overrides: object) -> str:
    payload = {**TRIP_PAYLOAD, **overrides}
    r = client.post("/v1/trips", json=payload)
    assert r.status_code == 201
    return r.json()["trip_id"]


def test_compute_recommendation_returns_200(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations", json={"trip_id": trip_id})
    assert response.status_code == 200


def test_compute_recommendation_response_shape(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations", json={"trip_id": trip_id})
    body = response.json()
    assert body["trip_id"] == trip_id
    assert "leave_home_at" in body
    assert "confidence" in body
    assert "confidence_score" in body
    assert 0.0 <= body["confidence_score"] <= 1.0
    assert "explanation" in body
    assert isinstance(body["segments"], list)
    for seg in body["segments"]:
        assert (
            "id" in seg
            and "label" in seg
            and "duration_minutes" in seg
            and "advice" in seg
        )
    assert "computed_at" in body


def test_compute_recommendation_missing_trip_id_returns_422(client: TestClient) -> None:
    response = client.post("/v1/recommendations", json={})
    assert response.status_code == 422


def test_compute_recommendation_unknown_trip_returns_404(client: TestClient) -> None:
    response = client.post(
        "/v1/recommendations", json={"trip_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 404


def test_recompute_recommendation_returns_200(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations/recompute", json={"trip_id": trip_id})
    assert response.status_code == 200


def test_recompute_recommendation_response_shape(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations/recompute", json={"trip_id": trip_id})
    body = response.json()
    assert body["trip_id"] == trip_id
    assert "leave_home_at" in body
    assert "confidence_score" in body


def test_recompute_with_reason_reflects_in_explanation(client: TestClient) -> None:
    trip_id = _create_trip(client)
    payload = {"trip_id": trip_id, "reason": "traffic_update"}
    response = client.post("/v1/recommendations/recompute", json=payload)
    body = response.json()
    assert "traffic_update" in body["explanation"]


def test_recompute_without_reason_is_valid(client: TestClient) -> None:
    trip_id = _create_trip(client)
    response = client.post("/v1/recommendations/recompute", json={"trip_id": trip_id})
    assert response.status_code == 200


def test_recompute_missing_trip_id_returns_422(client: TestClient) -> None:
    response = client.post("/v1/recommendations/recompute", json={})
    assert response.status_code == 422


def test_recompute_with_preference_overrides_changes_leave_home_at(
    client: TestClient,
) -> None:
    trip_id = _create_trip(client, bag_count=0, confidence_profile="sweet")
    r1 = client.post("/v1/recommendations/recompute", json={"trip_id": trip_id})
    assert r1.status_code == 200
    leave1 = r1.json()["leave_home_at"]

    r2 = client.post(
        "/v1/recommendations/recompute",
        json={
            "trip_id": trip_id,
            "preference_overrides": {"bag_count": 2, "traveling_with_children": True},
        },
    )
    assert r2.status_code == 200
    leave2 = r2.json()["leave_home_at"]
    # More bags + kids => earlier leave_home_at
    assert leave2 < leave1


def test_more_bags_produces_earlier_leave_home_at(client: TestClient) -> None:
    trip_id_low = _create_trip(client, bag_count=0)
    trip_id_high = _create_trip(client, bag_count=3)
    r_low = client.post("/v1/recommendations", json={"trip_id": trip_id_low})
    r_high = client.post("/v1/recommendations", json={"trip_id": trip_id_high})
    assert r_low.status_code == 200 and r_high.status_code == 200
    leave_low = r_low.json()["leave_home_at"]
    leave_high = r_high.json()["leave_home_at"]
    assert leave_high < leave_low


def test_risk_profile_produces_later_leave_home_at_than_safety(
    client: TestClient,
) -> None:
    trip_id_safety = _create_trip(client, confidence_profile="safety")
    trip_id_risk = _create_trip(client, confidence_profile="risk")
    r_safety = client.post("/v1/recommendations", json={"trip_id": trip_id_safety})
    r_risk = client.post("/v1/recommendations", json={"trip_id": trip_id_risk})
    assert r_safety.status_code == 200 and r_risk.status_code == 200
    leave_safety = r_safety.json()["leave_home_at"]
    leave_risk = r_risk.json()["leave_home_at"]
    assert leave_risk > leave_safety
