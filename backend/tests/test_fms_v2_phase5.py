"""Phase 5 guards: FMS v2 active app path does not depend on SQLite."""


def test_active_app_does_not_register_sqlite_backed_company_routes(app):
    paths = {getattr(route, "path", None) for route in app.routes}

    assert "/api/companies/{company_id}" not in paths
    assert "/api/companies/{company_id}/support" not in paths
    assert "/api/auth/verify-client-code" in paths
    assert "/api/chat/send" in paths


def test_health_checker_reports_no_sqlite_dependency(client):
    body = client.get("/api/health").json()

    assert body["database"] == "not_used"
    assert body["service"] == "healthy"
    assert "google_sheets" in body
    assert "llm_providers" in body


def test_llm_provider_health_does_not_expose_api_keys(monkeypatch):
    from app.fms_v2 import health
    from app.fms_v2.config import LlmProviderSettings

    monkeypatch.setattr(
        health,
        "get_llm_provider_settings",
        lambda: (
            LlmProviderSettings(
                name="cerebras",
                api_key="secret-key",
                model="gpt-oss-120b",
                base_url="https://api.cerebras.ai/v1",
            ),
        ),
    )

    result = health.check_llm_provider_config()

    assert result["configured_count"] == 1
    assert result["providers"] == [
        {
            "provider": "cerebras",
            "model": "gpt-oss-120b",
            "configured": True,
        }
    ]
    assert "secret-key" not in str(result)

