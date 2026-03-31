"""Tests for POST /v1/devices/register and DELETE /v1/devices/unregister."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.middleware.auth import get_required_user
from app.db import get_db
from app.main import app


class FakeUser:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.email = kwargs.get("email", "test@example.com")
        self.phone_number = kwargs.get("phone_number", "+1234567890")
        self.display_name = kwargs.get("display_name", "Test User")
        self.auth_provider = kwargs.get("auth_provider", "google")
        self.trip_count = kwargs.get("trip_count", 1)
        self.subscription_status = kwargs.get("subscription_status", "none")
        self.preferred_transport_mode = kwargs.get("preferred_transport_mode", None)
        self.preferred_security_access = kwargs.get("preferred_security_access", None)
        self.preferred_bag_count = kwargs.get("preferred_bag_count", None)
        self.preferred_children = kwargs.get("preferred_children", None)
        self.preferred_nav_app = kwargs.get("preferred_nav_app", None)
        self.preferred_rideshare_app = kwargs.get("preferred_rideshare_app", None)


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


def test_register_device_requires_auth(client: TestClient):
    resp = client.post("/v1/devices/register", json={"token": "abc", "platform": "ios"})
    assert resp.status_code == 401


def test_register_device_success(authed_client):
    client, _ = authed_client
    resp = client.post(
        "/v1/devices/register",
        json={"token": "test-token-123", "platform": "ios"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "registered"
    assert data["platform"] == "ios"


def test_register_device_invalid_platform(authed_client):
    client, _ = authed_client
    resp = client.post(
        "/v1/devices/register",
        json={"token": "test-token-123", "platform": "blackberry"},
    )
    assert resp.status_code == 422


def test_unregister_device_requires_auth(client: TestClient):
    resp = client.request("DELETE", "/v1/devices/unregister", json={"token": "abc"})
    assert resp.status_code == 401


def test_unregister_device_success(authed_client):
    client, _ = authed_client
    resp = client.request(
        "DELETE",
        "/v1/devices/unregister",
        json={"token": "test-token-123"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "unregistered"
