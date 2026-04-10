from unittest.mock import patch

from fastapi.testclient import TestClient


class TestRateLimit:
    @patch("app.api.routes.auth._get_supabase", return_value=None)
    def test_auth_rate_limited_after_10_requests(self, _mock, client: TestClient):
        """Auth endpoints should return 429 after 10 requests per minute from same IP."""
        # Reset limiter storage to avoid pollution from/to other tests
        from app.core.rate_limit import limiter
        limiter.reset()

        for i in range(10):
            resp = client.post("/v1/auth/send-otp", json={"phone_number": "+1234567890"})
            assert resp.status_code in (503, 200), f"Request {i+1} unexpected: {resp.status_code}"

        # 11th request should be rate-limited
        resp = client.post("/v1/auth/send-otp", json={"phone_number": "+1234567890"})
        assert resp.status_code == 429
        assert resp.json()["code"] == "RATE_LIMITED"

        # Reset after test to not affect subsequent tests
        limiter.reset()


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
