"""Tests for Apple Sign In social auth flow."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.integrations.apple_auth import AppleTokenClaims


# --- Helpers ---


def _mock_db_session(existing_user=None):
    """Return an async DB session mock that simulates SQLAlchemy queries."""
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = existing_user
    session.execute.return_value = result_mock
    return session


def _make_user(**overrides):
    """Create a mock User row."""
    defaults = {
        "id": uuid.uuid4(),
        "email": "jane@example.com",
        "apple_user_id": "apple.sub.123",
        "auth_provider": "apple",
        "display_name": "Jane Doe",
        "trip_count": 0,
        "subscription_status": "none",
        "phone_number": None,
    }
    defaults.update(overrides)
    user = MagicMock()
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


# --- Tests: Apple token verification triggers ---


class TestAppleAuthEndpoint:
    """POST /v1/auth/social with provider=apple."""

    @patch(
        "app.api.routes.auth.verify_apple_identity_token",
        return_value=AppleTokenClaims(sub="apple.sub.123", email="jane@example.com"),
    )
    def test_apple_auth_basic(self, _mock_verify, client: TestClient):
        """Apple auth returns expected response shape (no DB)."""
        response = client.post(
            "/v1/auth/social",
            json={
                "provider": "apple",
                "id_token": "fake.apple.jwt",
                "given_name": "Jane",
                "family_name": "Doe",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert "user_id" in data
        assert data["trip_count"] == 0
        assert data["tier"] == "pro"
        assert data["display_name"] == "Jane Doe"
        assert data["email"] == "jane@example.com"

    @patch(
        "app.api.routes.auth.verify_apple_identity_token",
        side_effect=Exception("Invalid token"),
    )
    def test_apple_auth_invalid_token(self, _mock_verify, client: TestClient):
        """Invalid Apple token returns 401."""
        response = client.post(
            "/v1/auth/social",
            json={"provider": "apple", "id_token": "bad.token"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication failed"

    @patch(
        "app.api.routes.auth.verify_apple_identity_token",
        return_value=AppleTokenClaims(sub="apple.sub.456", email=None),
    )
    def test_apple_auth_no_email_no_db(self, _mock_verify, client: TestClient):
        """Apple auth with no email still returns 200 when no DB."""
        response = client.post(
            "/v1/auth/social",
            json={"provider": "apple", "id_token": "fake.apple.jwt"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] is None


class TestAppleAuthWithDB:
    """Apple auth tests that simulate DB interactions."""

    @pytest.fixture
    def _patch_verify(self):
        with patch(
            "app.api.routes.auth.verify_apple_identity_token",
            return_value=AppleTokenClaims(
                sub="apple.sub.123", email="jane@example.com"
            ),
        ) as m:
            yield m

    def test_creates_new_user(self, _patch_verify, client: TestClient):
        """Creates a new user when no existing user found."""
        session = _mock_db_session(existing_user=None)

        # After add + commit + refresh, simulate the new row
        new_user = _make_user()
        async def _refresh(obj):
            for attr in ("id", "email", "apple_user_id", "auth_provider",
                         "display_name", "trip_count", "subscription_status"):
                setattr(obj, attr, getattr(new_user, attr))
        session.refresh.side_effect = _refresh

        from app.db import get_db
        from app.main import app

        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        try:
            response = client.post(
                "/v1/auth/social",
                json={
                    "provider": "apple",
                    "id_token": "fake.jwt",
                    "given_name": "Jane",
                    "family_name": "Doe",
                },
            )
            assert response.status_code == 200
            # db.add was called (new user created)
            session.add.assert_called_once()
            session.commit.assert_called()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_existing_user_no_name_overwrite(self, _patch_verify, client: TestClient):
        """Existing user's display_name is NOT overwritten with empty names."""
        existing = _make_user(display_name="Jane Doe")
        session = _mock_db_session(existing_user=existing)

        async def _refresh(obj):
            pass  # Keep existing attrs
        session.refresh.side_effect = _refresh

        from app.db import get_db
        from app.main import app

        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        try:
            response = client.post(
                "/v1/auth/social",
                json={
                    "provider": "apple",
                    "id_token": "fake.jwt",
                    # No given_name/family_name — simulates subsequent sign-in
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["display_name"] == "Jane Doe"
            # add should NOT be called (user already exists)
            session.add.assert_not_called()
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_no_email_falls_back_to_apple_sub(self, client: TestClient):
        """When Apple token has no email, lookup falls back to apple_user_id."""
        existing = _make_user(email=None, apple_user_id="apple.sub.noemail")

        # email is None so email lookup is skipped; only apple_user_id query runs
        session = AsyncMock()
        result_found = MagicMock()
        result_found.scalar_one_or_none.return_value = existing
        session.execute.return_value = result_found

        async def _refresh(obj):
            pass
        session.refresh.side_effect = _refresh

        from app.db import get_db
        from app.main import app

        async def _override():
            yield session

        with patch(
            "app.api.routes.auth.verify_apple_identity_token",
            return_value=AppleTokenClaims(
                sub="apple.sub.noemail", email=None
            ),
        ):
            app.dependency_overrides[get_db] = _override
            try:
                response = client.post(
                    "/v1/auth/social",
                    json={"provider": "apple", "id_token": "fake.jwt"},
                )
                assert response.status_code == 200
                # Should find user via apple_user_id, not create new
                session.add.assert_not_called()
            finally:
                app.dependency_overrides.pop(get_db, None)


class TestGoogleAuthUnchanged:
    """Verify Google auth path is not broken."""

    @patch("app.api.routes.auth._get_supabase", return_value=None)
    def test_google_still_uses_supabase(self, _mock, client: TestClient):
        """Google auth still goes through Supabase path."""
        response = client.post(
            "/v1/auth/social",
            json={"provider": "google", "id_token": "google.token"},
        )
        # 503 because supabase is None — proves it went through Supabase path
        assert response.status_code == 503
        assert response.json()["detail"] == "Auth service not configured"
