"""
Smoke tests — the cheapest possible guard that the app actually boots.

These would have caught two real bugs that shipped:
  1. NameError on import
  2. /api/health returning 404 because the static mount shadowed the route.

If either regresses, these tests fail immediately.
"""


def test_app_imports():
    """The app module must import without raising (catches missing imports)."""
    import app.main

    assert app.main.app is not None


def test_health_route_is_registered(app):
    """/api/health must be a real route, not swallowed by the static mount."""
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/health" in paths


def test_health_endpoint_responds(client):
    """GET /api/health returns 200 and a status payload (not 404)."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"healthy", "degraded"}
    assert "database" in body
    assert "google_sheets" in body
    assert "llm_providers" in body


def test_health_reports_fms_v2_dependencies_without_sqlite(client):
    """FMS v2 health reports Sheets/providers and does not check SQLite."""
    body = client.get("/api/health").json()
    assert body["database"] == "not_used"
    assert body["google_sheets"]["status"] == "healthy"
    assert body["google_sheets"]["checked_sheet"] == "FMS1"
    assert body["llm_providers"]["configured_count"] == 1
