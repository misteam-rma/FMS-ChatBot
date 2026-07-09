"""
Shared pytest fixtures for the Finance FMS backend test suite.

These fixtures spin up the FastAPI app with live external health checks stubbed,
so tests never touch Google Sheets or LLM providers unless a test opts in.
"""

import os

import pytest

# Force development mode so validate_production_secrets() never aborts the
# process during import, regardless of the developer's local .env.
os.environ.setdefault("APP_ENV", "development")


class FakeResult:
    """Mimics the subset of SQLAlchemy Result used by the routers."""

    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class FakeSession:
    """Minimal async DB session stand-in.

    `execute` returns a configurable value so route logic that selects the
    active Company / DatabaseConnection can run without a real database.
    """

    def __init__(self, result_value=None):
        self._result_value = result_value

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self._result_value)

    async def close(self):
        return None


@pytest.fixture
def app():
    """Return the FastAPI app instance (imported lazily to apply env first)."""
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture
def client(app):
    """A TestClient with live health checks overridden and lifespan disabled."""
    from fastapi.testclient import TestClient
    from app.main import get_health_checker

    async def _fake_health_check():
        return {
            "status": "healthy",
            "service": "healthy",
            "database": "not_used",
            "google_sheets": {
                "status": "healthy",
                "workbook_id": "test-workbook",
                "checked_sheet": "FMS1",
                "header_row": 6,
            },
            "llm_providers": {
                "status": "configured",
                "configured_count": 1,
                "providers": [
                    {
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "configured": True,
                    }
                ],
            },
        }

    def _fake_get_health_checker():
        return _fake_health_check

    app.dependency_overrides[get_health_checker] = _fake_get_health_checker
    # No `with` block => lifespan startup/shutdown is skipped.
    # raise_server_exceptions=False so 500s come back as responses, not raises.
    test_client = TestClient(app, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()
