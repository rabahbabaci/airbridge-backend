from fastapi.testclient import TestClient


def test_version_returns_200(client: TestClient) -> None:
    response = client.get("/version")
    assert response.status_code == 200


def test_version_response_shape(client: TestClient) -> None:
    body = client.get("/version").json()
    assert "app_name" in body
    assert "version" in body
    assert "environment" in body


def test_version_values(client: TestClient) -> None:
    body = client.get("/version").json()
    assert body["app_name"] == "airbridge-backend"
    assert body["version"] == "0.1.0"
