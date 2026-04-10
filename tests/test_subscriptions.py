"""Tests for Stripe subscription endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi.testclient import TestClient

from app.api.middleware.auth import get_required_user
from app.db import get_db
from app.main import app


class FakeUser:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.email = kwargs.get("email", "test@example.com")
        self.phone_number = kwargs.get("phone_number", "+1234567890")
        self.trip_count = kwargs.get("trip_count", 1)
        self.subscription_status = kwargs.get("subscription_status", "none")
        self.stripe_customer_id = kwargs.get("stripe_customer_id", None)


async def _override_get_db():
    yield None


@pytest.fixture
def authed_client():
    mock_user = FakeUser()

    async def _override():
        return mock_user

    app.dependency_overrides[get_required_user] = _override
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_user
    app.dependency_overrides.pop(get_required_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def pro_client():
    mock_user = FakeUser(
        subscription_status="active",
        stripe_customer_id="cus_test123",
    )

    async def _override():
        return mock_user

    app.dependency_overrides[get_required_user] = _override
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app), mock_user
    app.dependency_overrides.pop(get_required_user, None)
    app.dependency_overrides.pop(get_db, None)


class TestCheckout:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.post("/v1/subscriptions/checkout", json={
            "price_type": "monthly",
            "success_url": "https://app.airbridge.com/success",
            "cancel_url": "https://app.airbridge.com/cancel",
        })
        assert resp.status_code == 401

    @patch("app.api.routes.subscriptions.settings")
    def test_stripe_not_configured_returns_503(self, mock_settings, authed_client):
        mock_settings.stripe_secret_key = ""
        client, _ = authed_client
        resp = client.post("/v1/subscriptions/checkout", json={
            "price_type": "monthly",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        })
        assert resp.status_code == 503

    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_checkout_creates_session(self, mock_settings, mock_stripe, authed_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        mock_settings.stripe_price_monthly = "price_monthly_123"
        mock_settings.stripe_price_annual = "price_annual_123"

        mock_customer = MagicMock()
        mock_customer.id = "cus_new_123"
        mock_stripe.Customer.create.return_value = mock_customer

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/session123"
        mock_stripe.checkout.Session.create.return_value = mock_session

        client, _ = authed_client
        resp = client.post("/v1/subscriptions/checkout", json={
            "price_type": "monthly",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        })
        assert resp.status_code == 200
        assert resp.json()["checkout_url"] == "https://checkout.stripe.com/session123"

    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_checkout_annual_uses_annual_price(self, mock_settings, mock_stripe, authed_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        mock_settings.stripe_price_monthly = "price_monthly_123"
        mock_settings.stripe_price_annual = "price_annual_123"

        mock_customer = MagicMock()
        mock_customer.id = "cus_new_123"
        mock_stripe.Customer.create.return_value = mock_customer

        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/session456"
        mock_stripe.checkout.Session.create.return_value = mock_session

        client, _ = authed_client
        resp = client.post("/v1/subscriptions/checkout", json={
            "price_type": "annual",
            "success_url": "https://example.com/success",
            "cancel_url": "https://example.com/cancel",
        })
        assert resp.status_code == 200
        mock_stripe.checkout.Session.create.assert_called_once()
        call_kwargs = mock_stripe.checkout.Session.create.call_args[1]
        assert call_kwargs["line_items"][0]["price"] == "price_annual_123"


class TestWebhook:
    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_invalid_signature_returns_400(self, mock_settings, mock_stripe):
        mock_settings.stripe_webhook_secret = "whsec_test"
        mock_stripe.error.SignatureVerificationError = type(
            "SignatureVerificationError", (Exception,), {}
        )
        mock_stripe.Webhook.construct_event.side_effect = (
            mock_stripe.error.SignatureVerificationError("bad sig")
        )
        client = TestClient(app)
        resp = client.post(
            "/v1/subscriptions/webhook",
            content=b'{"type": "test"}',
            headers={"stripe-signature": "bad_sig"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "INVALID_SIGNATURE"

    @patch("app.db.async_session_factory", None)
    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_valid_webhook_returns_200(self, mock_settings, mock_stripe):
        mock_settings.stripe_webhook_secret = "whsec_test"
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {"object": {"customer": "cus_test"}},
        }
        client = TestClient(app)
        resp = client.post(
            "/v1/subscriptions/webhook",
            content=b'{"type": "checkout.session.completed"}',
            headers={"stripe-signature": "valid_sig"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @patch("app.api.routes.subscriptions.settings")
    def test_webhook_secret_not_configured_returns_503(self, mock_settings):
        mock_settings.stripe_webhook_secret = ""
        client = TestClient(app)
        resp = client.post(
            "/v1/subscriptions/webhook",
            content=b'{}',
            headers={"stripe-signature": "sig"},
        )
        assert resp.status_code == 503

    @pytest.mark.parametrize(
        "event_type,expected_status",
        [
            ("checkout.session.completed", "active"),
            ("customer.subscription.deleted", "cancelled"),
            ("invoice.payment_failed", "past_due"),
        ],
    )
    @patch("app.api.routes.subscriptions.settings")
    @patch("app.api.routes.subscriptions.stripe")
    def test_webhook_real_stripe_object_updates_user(
        self, mock_stripe, mock_settings, event_type, expected_status
    ):
        """Regression: Stripe SDK v15 StripeObject does not inherit from dict.

        Earlier code called data.get("customer") which raised
        AttributeError because .get is not an attribute. This test
        constructs a real StripeObject (not a dict) so the handler
        exercises the actual prod code path.
        """
        mock_settings.stripe_webhook_secret = "whsec_test"
        event_obj = stripe.StripeObject.construct_from(
            {
                "type": event_type,
                "data": {"object": {"customer": "cus_test_123"}},
            },
            "sk_test",
        )
        mock_stripe.Webhook.construct_event.return_value = event_obj

        fake_user = FakeUser(stripe_customer_id="cus_test_123")

        exec_result = MagicMock()
        exec_result.scalar_one_or_none = MagicMock(return_value=fake_user)
        fake_session = MagicMock()
        fake_session.execute = AsyncMock(return_value=exec_result)
        fake_session.commit = AsyncMock()
        fake_cm = MagicMock()
        fake_cm.__aenter__ = AsyncMock(return_value=fake_session)
        fake_cm.__aexit__ = AsyncMock(return_value=None)
        fake_factory = MagicMock(return_value=fake_cm)

        with patch("app.db.async_session_factory", fake_factory):
            client = TestClient(app)
            resp = client.post(
                "/v1/subscriptions/webhook",
                content=b"{}",
                headers={"stripe-signature": "valid"},
            )

        assert resp.status_code == 200
        assert fake_user.subscription_status == expected_status
        fake_session.commit.assert_awaited()


class TestSubscriptionStatus:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.get("/v1/subscriptions/status")
        assert resp.status_code == 401

    def test_returns_status_for_trial_user(self, authed_client):
        client, _ = authed_client
        resp = client.get("/v1/subscriptions/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["subscription_status"] == "none"
        assert data["tier"] == "pro"
        assert data["trial_trips_remaining"] == 2
        assert data["stripe_customer_id"] is None
        assert data["current_period_end"] is None

    def test_returns_status_for_pro_user(self, pro_client):
        client, _ = pro_client
        resp = client.get("/v1/subscriptions/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["subscription_status"] == "active"
        assert data["tier"] == "pro"
        assert data["trial_trips_remaining"] is None
        assert data["stripe_customer_id"] == "cus_test123"

    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_pro_user_gets_current_period_end(self, mock_settings, mock_stripe, pro_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        mock_sub = MagicMock()
        mock_sub.current_period_end = 1712000000
        mock_subs_list = MagicMock()
        mock_subs_list.data = [mock_sub]
        mock_stripe.Subscription.list.return_value = mock_subs_list

        client, _ = pro_client
        resp = client.get("/v1/subscriptions/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_period_end"] == 1712000000
        mock_stripe.Subscription.list.assert_called_once_with(
            customer="cus_test123", status="active", limit=1,
        )

    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_stripe_error_returns_null_period_end(self, mock_settings, mock_stripe, pro_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        mock_stripe.Subscription.list.side_effect = Exception("Stripe down")

        client, _ = pro_client
        resp = client.get("/v1/subscriptions/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_period_end"] is None

    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_no_active_subscriptions_returns_null(self, mock_settings, mock_stripe, pro_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        mock_subs_list = MagicMock()
        mock_subs_list.data = []
        mock_stripe.Subscription.list.return_value = mock_subs_list

        client, _ = pro_client
        resp = client.get("/v1/subscriptions/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_period_end"] is None


class TestPortal:
    def test_no_auth_returns_401(self, client: TestClient):
        resp = client.post("/v1/subscriptions/portal")
        assert resp.status_code == 401

    @patch("app.api.routes.subscriptions.settings")
    def test_no_stripe_customer_returns_400(self, mock_settings, authed_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        client, _ = authed_client
        resp = client.post("/v1/subscriptions/portal")
        assert resp.status_code == 400
        assert resp.json()["code"] == "NO_SUBSCRIPTION"

    @patch("app.api.routes.subscriptions.stripe")
    @patch("app.api.routes.subscriptions.settings")
    def test_portal_creates_session(self, mock_settings, mock_stripe, pro_client):
        mock_settings.stripe_secret_key = "sk_test_123"
        mock_session = MagicMock()
        mock_session.url = "https://billing.stripe.com/portal123"
        mock_stripe.billing_portal.Session.create.return_value = mock_session

        client, _ = pro_client
        resp = client.post("/v1/subscriptions/portal")
        assert resp.status_code == 200
        assert resp.json()["portal_url"] == "https://billing.stripe.com/portal123"
