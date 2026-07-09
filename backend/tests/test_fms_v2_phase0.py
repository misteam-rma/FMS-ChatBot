"""Migration guards for the FMS v2 modules."""


def test_fms_v2_settings_define_only_allowed_sheets():
    from app.fms_v2.config import (
        DEFAULT_FMS_WORKBOOK_ID,
        FMS_CLIENT_CODE_COLUMNS,
        FMS_HEADER_ROW,
        FMS_SHEET_NAMES,
        get_fms_v2_settings,
    )

    settings = get_fms_v2_settings()

    assert DEFAULT_FMS_WORKBOOK_ID == "10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok"
    assert settings.sheet_names == ("FMS1", "FMS2", "FMS3", "FMS4")
    assert FMS_SHEET_NAMES == settings.sheet_names
    assert FMS_HEADER_ROW == 6
    assert FMS_CLIENT_CODE_COLUMNS == {
        "FMS1": "J",
        "FMS2": "B",
        "FMS3": "J",
        "FMS4": "B",
    }


def test_fms_v2_client_code_model_normalizes_code():
    from app.fms_v2.models import ClientCodeLoginRequest, FetchFmsRecordsInput

    login = ClientCodeLoginRequest(client_job_code="  hoacpl-f25f-tl01  ", phone="9993866117")
    fetch = FetchFmsRecordsInput(client_job_code="ABC   F25F   TL01")

    assert login.client_job_code == "HOACPL-F25F-TL01"
    assert fetch.client_job_code == "ABC F25F TL01"
    assert fetch.sheets == ["FMS1", "FMS2", "FMS3", "FMS4"]


def test_fms_v2_auth_and_chat_are_registered(app):
    from app.routers.fms_v2_auth_router import router as auth_router
    from app.routers.fms_v2_chat_router import router as chat_router

    auth_paths = {route.path for route in auth_router.routes}
    chat_paths = {route.path for route in chat_router.routes}

    assert "/api/auth/verify-client-code" in auth_paths
    assert "/api/auth/verify-admin" in auth_paths
    assert "/api/chat/send" in chat_paths

    registered_routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) in {
            "/api/auth/verify-client-code",
            "/api/auth/verify-admin",
            "/api/chat/send",
        }
    ]
    registered_modules = {
        getattr(getattr(route, "endpoint", None), "__module__", "")
        for route in registered_routes
    }

    assert "app.routers.fms_v2_auth_router" in registered_modules
    assert "app.routers.fms_v2_chat_router" in registered_modules
    assert "/api/auth/verify-client-code" in {
        getattr(route, "path", None) for route in app.routes
    }
