"""Pydantic models for the FMS v2 backend skeleton."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.fms_v2.config import FMS_SHEET_NAMES, FmsSheetName


class ClientCodeLoginRequest(BaseModel):
    client_job_code: str = Field(..., min_length=3, max_length=80)

    @field_validator("client_job_code")
    @classmethod
    def normalize_client_job_code(cls, value: str) -> str:
        return " ".join(str(value or "").strip().upper().split())


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
    sheet_name: FmsSheetName
    row_number: int = Field(..., ge=1)
    column_name: str
    column_letter: str | None = None


class FmsRecord(BaseModel):
    sheet_name: FmsSheetName
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
        return " ".join(str(value or "").strip().upper().split())


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
