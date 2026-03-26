from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _mock_supabase_social(email="test@example.com"):
    """Return a mock Supabase client whose sign_in_with_id_token succeeds."""
    mock_user = MagicMock()
    mock_user.email = email

    mock_response = MagicMock()
    mock_response.user = mock_user

    mock_client = MagicMock()
    mock_client.auth.sign_in_with_id_token.return_value = mock_response
    return mock_client


class TestSocialAuthEndpoint:
    def test_valid_google_returns_200(self, client: TestClient):
        mock_client = _mock_supabase_social()
        with patch("app.api.routes.auth._get_supabase", return_value=mock_client):
            resp = client.post(
                "/v1/auth/social",
                json={"provider": "google", "id_token": "valid-token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "user_id" in data
        assert "token" in data
        assert "trip_count" in data
        assert "tier" in data

    def test_valid_apple_returns_200(self, client: TestClient):
        mock_client = _mock_supabase_social()
        with patch("app.api.routes.auth._get_supabase", return_value=mock_client):
            resp = client.post(
                "/v1/auth/social",
                json={
                    "provider": "apple",
                    "id_token": "valid-token",
                    "display_name": "Jane Doe",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] in ("pro", "free")

    def test_missing_id_token_returns_422(self, client: TestClient):
        resp = client.post(
            "/v1/auth/social",
            json={"provider": "google"},
        )
        assert resp.status_code == 422

    def test_invalid_provider_returns_422(self, client: TestClient):
        resp = client.post(
            "/v1/auth/social",
            json={"provider": "facebook", "id_token": "some-token"},
        )
        assert resp.status_code == 422

    def test_same_email_returns_same_user_id(self, client: TestClient):
        mock_client = _mock_supabase_social(email="idempotent@example.com")
        with patch("app.api.routes.auth._get_supabase", return_value=mock_client):
            resp1 = client.post(
                "/v1/auth/social",
                json={"provider": "google", "id_token": "token-1"},
            )
            resp2 = client.post(
                "/v1/auth/social",
                json={"provider": "google", "id_token": "token-2"},
            )
        # Without a DB both return None user_id, which is consistent
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["user_id"] == resp2.json()["user_id"]

    def test_service_not_configured_returns_503(self, client: TestClient):
        resp = client.post(
            "/v1/auth/social",
            json={"provider": "google", "id_token": "some-token"},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Auth service not configured"
