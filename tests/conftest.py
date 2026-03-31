import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on the path so `app` is importable without installation
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402


async def _override_get_db():
    yield None


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
