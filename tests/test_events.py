from fastapi.testclient import TestClient


def test_record_event_basic(client: TestClient) -> None:
    resp = client.post("/v1/events", json={"event_name": "flight_searched"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded"


def test_record_event_with_metadata(client: TestClient) -> None:
    resp = client.post(
        "/v1/events",
        json={
            "event_name": "recommendation_viewed",
            "metadata": {"flight": "UA300"},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded"


def test_record_event_anonymous(client: TestClient) -> None:
    """Events work without auth header (anonymous tracking)."""
    resp = client.post("/v1/events", json={"event_name": "app_opened"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded"


def test_record_event_missing_event_name(client: TestClient) -> None:
    resp = client.post("/v1/events", json={})
    assert resp.status_code == 422
