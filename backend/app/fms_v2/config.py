"""Configuration constants for the FMS v2 backend skeleton."""

from dataclasses import dataclass, field
from typing import Literal

from app.config import settings


FmsSheetName = Literal["FMS1", "FMS2", "FMS3", "FMS4"]

DEFAULT_FMS_WORKBOOK_ID = "10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok"
FMS_SHEET_NAMES: tuple[FmsSheetName, ...] = ("FMS1", "FMS2", "FMS3", "FMS4")
FMS_HEADER_ROW = 6
FMS_CLIENT_CODE_COLUMNS: dict[FmsSheetName, str] = {
    "FMS1": "J",
    "FMS2": "B",
    "FMS3": "J",
    "FMS4": "B",
}

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

LlmProviderName = Literal["cerebras", "groq", "nvidia", "openai"]
DEFAULT_LLM_PROVIDER_ORDER: tuple[LlmProviderName, ...] = (
    "cerebras",
    "groq",
    "nvidia",
    "openai",
)

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
# Fail fast to the next provider so total fallback latency stays low.
FMS_V2_LLM_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class FmsV2Settings:
    """Resolved FMS v2 settings.

    Phase 0 only defines the shape. Later phases will use these values for
    deterministic sheet access and LLM provider fallback.
    """

    workbook_id: str
    sheet_names: tuple[FmsSheetName, ...]
    header_row: int
    client_code_columns: dict[FmsSheetName, str]
    admin_username: str
    admin_password: str


@dataclass(frozen=True)
class LlmProviderSettings:
    """One OpenAI-compatible chat provider configuration."""

    name: LlmProviderName
    api_key: str = field(repr=False)
    model: str
    base_url: str
    timeout_seconds: float = FMS_V2_LLM_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)


def get_fms_v2_settings() -> FmsV2Settings:
    """Return FMS v2 settings without mutating legacy application settings."""

    return FmsV2Settings(
        workbook_id=settings.google_sheet_id or DEFAULT_FMS_WORKBOOK_ID,
        sheet_names=FMS_SHEET_NAMES,
        header_row=FMS_HEADER_ROW,
        client_code_columns=FMS_CLIENT_CODE_COLUMNS.copy(),
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
    )


def get_llm_provider_settings() -> tuple[LlmProviderSettings, ...]:
    """Return configured provider candidates in the intended fallback order."""

    return (
        LlmProviderSettings(
            name="cerebras",
            api_key=settings.cerebras_api_key,
            model=settings.cerebras_model,
            base_url=CEREBRAS_BASE_URL,
        ),
        LlmProviderSettings(
            name="groq",
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=GROQ_BASE_URL,
        ),
        LlmProviderSettings(
            name="nvidia",
            api_key=settings.nvidia_api_key,
            model=settings.nvidia_model,
            base_url=NVIDIA_BASE_URL,
        ),
        LlmProviderSettings(
            name="openai",
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=OPENAI_BASE_URL,
        ),
    )
