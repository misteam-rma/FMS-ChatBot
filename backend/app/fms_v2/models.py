"""Pydantic models for the FMS v2 backend skeleton."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.fms_v2.config import FMS_SHEET_NAMES, FmsSheetName

# Records normally come from FMS1-FMS4, but ADMIN queries may also cite extra
# tabs (RAW DATA client master, dashboards). Allow those source names too.
SourceSheetName = FmsSheetName | Literal[
    "RAW DATA", "NEW DASH", "Completed Dash", "RUF Help Sheet", "Sanction Letter"
]

# Fold Unicode dash variants (non-breaking hyphen, en/em dash, etc.) to ASCII
# "-" so codes match regardless of how the dash was typed/pasted. Kept here to
# avoid importing sheets.py (which imports this module).
_DASH_VARIANTS = str.maketrans({c: "-" for c in "‐‑‒–—―−﹘﹣－"})


def _normalize_code(value: str) -> str:
    text = str(value or "").translate(_DASH_VARIANTS)
    return " ".join(text.strip().upper().split())


class ClientCodeLoginRequest(BaseModel):
    client_job_code: str = Field(..., min_length=3, max_length=80)
    # Phone is required for phone+code pairing auth. Optional-typed so older
    # clients that omit it get a clear 422 rather than a silent mismatch.
    phone: str = Field(..., min_length=6, max_length=20)

    @field_validator("client_job_code")
    @classmethod
    def normalize_client_job_code(cls, value: str) -> str:
        return _normalize_code(value)


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class FmsV2LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee_id: str
    employee_name: str = ""
    mobile_number: str = ""
    user_type: Literal["client", "admin"]
    client_job_code: str | None = None


class TokenPayload(BaseModel):
    employee_id: str
    employee_name: str = ""
    mobile_number: str = ""
    user_type: Literal["client", "admin"] = "client"
    client_job_code: str | None = None


class ToolError(BaseModel):
    sheet_name: FmsSheetName | None = None
    code: str
    message: str


class SourceColumn(BaseModel):
    sheet_name: SourceSheetName
    row_number: int = Field(..., ge=1)
    column_name: str
    column_letter: str | None = None


class FmsRecord(BaseModel):
    sheet_name: SourceSheetName
    row_number: int = Field(..., ge=1)
    client_job_code: str
    client_name: str = ""
    base_fields: dict[str, Any] = Field(default_factory=dict)
    step_fields: dict[str, Any] = Field(default_factory=dict)
    source_columns: dict[str, SourceColumn] = Field(default_factory=dict)


class FetchFmsRecordsInput(BaseModel):
    client_job_code: str = Field(..., min_length=3, max_length=80)
    sheets: list[FmsSheetName] = Field(default_factory=lambda: list(FMS_SHEET_NAMES))

    @field_validator("client_job_code")
    @classmethod
    def normalize_client_job_code(cls, value: str) -> str:
        return _normalize_code(value)


class FetchFmsRecordsOutput(BaseModel):
    ok: bool
    client_job_code: str
    records: list[FmsRecord] = Field(default_factory=list)
    errors: list[ToolError] = Field(default_factory=list)
    latency_ms: int = Field(..., ge=0)


class FmsV2ChatMessage(BaseModel):
    message: str = Field(..., min_length=1)
    chat_history: list[dict[str, str]] | None = None


class FmsV2ChatResponse(BaseModel):
    reply: str
    actions: list[dict[str, Any]] | None = None
    notifications: list[dict[str, Any]] | None = None


class FmsV2IntentRequest(BaseModel):
    """A button-driven menu intent (deterministic, no LLM)."""

    intent: str = Field(..., min_length=1, max_length=40)
