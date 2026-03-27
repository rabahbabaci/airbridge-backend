from unittest.mock import patch

from fastapi.testclient import TestClient


@patch("app.api.routes.auth._get_supabase", return_value=None)
def test_send_otp_service_not_configured(_mock, client: TestClient) -> None:
    response = client.post(
        "/v1/auth/send-otp", json={"phone_number": "+1234567890"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Auth service not configured"


@patch("app.api.routes.auth._get_supabase", return_value=None)
def test_verify_otp_service_not_configured(_mock, client: TestClient) -> None:
    response = client.post(
        "/v1/auth/verify-otp",
        json={"phone_number": "+1234567890", "code": "123456"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Auth service not configured"


def test_send_otp_missing_phone(client: TestClient) -> None:
    response = client.post("/v1/auth/send-otp", json={})
    assert response.status_code == 422


def test_verify_otp_missing_fields(client: TestClient) -> None:
    response = client.post("/v1/auth/verify-otp", json={})
    assert response.status_code == 422
