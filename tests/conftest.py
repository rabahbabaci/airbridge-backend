import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Ensure src/ is on the path so `app` is importable without installation
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.db import Base, get_db  # noqa: E402
from app.db import models as _models  # noqa: E402, F401 — register all models with Base.metadata
from app.main import app  # noqa: E402
from app.api.middleware.auth import get_required_user  # noqa: E402


# ---------------------------------------------------------------------------
# Existing fixture (db=None) — untouched, used by existing tests
# ---------------------------------------------------------------------------

async def _override_get_db():
    yield None


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Async SQLite test DB infrastructure (Sprint 7)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_engine():
    """Session-scoped async engine backed by in-memory SQLite."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def test_session(test_engine):
    """Function-scoped async session with clean tables per test."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    # Create all tables fresh for each test
    async def _setup():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        return factory

    factory = asyncio.run(_setup())

    async def _make_session():
        async with factory() as session:
            yield session

    yield factory, _make_session


class FakeUser:
    """Reusable mock user for test fixtures."""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.email = kwargs.get("email", "test@example.com")
        self.phone_number = kwargs.get("phone_number", "+1234567890")
        self.trip_count = kwargs.get("trip_count", 0)
        self.subscription_status = kwargs.get("subscription_status", "none")
        self.stripe_customer_id = kwargs.get("stripe_customer_id", None)
        self.apple_user_id = kwargs.get("apple_user_id", None)
        self.auth_provider = kwargs.get("auth_provider", None)
        self.display_name = kwargs.get("display_name", None)
        self.preferred_transport_mode = kwargs.get("preferred_transport_mode", None)
        self.preferred_security_access = kwargs.get("preferred_security_access", None)
        self.preferred_bag_count = kwargs.get("preferred_bag_count", None)
        self.preferred_children = kwargs.get("preferred_children", None)
        self.preferred_nav_app = kwargs.get("preferred_nav_app", None)
        self.preferred_rideshare_app = kwargs.get("preferred_rideshare_app", None)


@pytest.fixture
def db_client(test_session):
    """TestClient backed by real async SQLite DB, no auth."""
    factory, _ = test_session

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    yield TestClient(app), factory
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def authed_db_client(test_session):
    """TestClient backed by real async SQLite DB + FakeUser auth."""
    factory, _ = test_session
    mock_user = FakeUser()

    async def _override_db():
        async with factory() as session:
            yield session

    async def _override_auth():
        return mock_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_required_user] = _override_auth
    yield TestClient(app), factory, mock_user
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_required_user, None)
