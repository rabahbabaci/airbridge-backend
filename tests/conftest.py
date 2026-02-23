import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure src/ is on the path so `app` is importable without installation
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
