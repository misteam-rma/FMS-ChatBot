"""Health checks for the FMS v2 API-only backend."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings as app_settings
from app.fms_v2.config import FMS_HEADER_ROW, get_fms_v2_settings, get_llm_provider_settings


logger = logging.getLogger("botivate_api.fms_v2.health")


async def check_fms_v2_health() -> dict[str, Any]:
    """Return service, Google Sheets, and LLM provider health.

    SQLite is intentionally not checked: FMS v2 auth and chat do not use it.
    """

    google_sheets = await check_google_sheets_connectivity()
    llm_providers = check_llm_provider_config()
    status = "healthy"
    if google_sheets["status"] != "healthy" or llm_providers["configured_count"] < 1:
        status = "degraded"

    return {
        "status": status,
        "service": "healthy",
        "database": "not_used",
        "google_sheets": google_sheets,
        "llm_providers": llm_providers,
    }


async def check_google_sheets_connectivity(timeout_seconds: float = 8) -> dict[str, Any]:
    """Validate that the configured service account can read the FMS workbook."""

    fms_settings = get_fms_v2_settings()
    if not app_settings.google_service_account_json:
        return {
            "status": "not_configured",
            "workbook_id": fms_settings.workbook_id,
            "checked_sheet": None,
            "error": "GOOGLE_SERVICE_ACCOUNT_JSON is not configured.",
        }

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_check_google_sheets_connectivity_sync),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        logger.warning("FMS v2 Google Sheets health check failed: %s", exc)
        return {
            "status": "unhealthy",
            "workbook_id": fms_settings.workbook_id,
            "checked_sheet": "FMS1",
            "error": str(exc),
        }


def _check_google_sheets_connectivity_sync() -> dict[str, Any]:
    import gspread

    fms_settings = get_fms_v2_settings()
    try:
        service_account_info = json.loads(app_settings.google_service_account_json)
    except json.JSONDecodeError as exc:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc

    client = gspread.service_account_from_dict(service_account_info)
    spreadsheet = client.open_by_key(fms_settings.workbook_id)
    worksheet = spreadsheet.worksheet("FMS1")
    headers = worksheet.row_values(FMS_HEADER_ROW)
    if "Client Job Code" not in headers:
        raise ValueError("FMS1 header row does not contain Client Job Code.")

    return {
        "status": "healthy",
        "workbook_id": fms_settings.workbook_id,
        "checked_sheet": "FMS1",
        "header_row": FMS_HEADER_ROW,
    }


def check_llm_provider_config() -> dict[str, Any]:
    """Report configured LLM providers without exposing secret values."""

    providers = []
    configured_count = 0
    for provider in get_llm_provider_settings():
        configured = provider.is_configured
        configured_count += int(configured)
        providers.append(
            {
                "provider": provider.name,
                "model": provider.model,
                "configured": configured,
            }
        )

    return {
        "status": "configured" if configured_count else "not_configured",
        "configured_count": configured_count,
        "providers": providers,
    }

