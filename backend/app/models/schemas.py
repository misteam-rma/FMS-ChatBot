"""
Botivate HR Support - Pydantic Schemas
Request/Response models for all API endpoints.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from app.models.models import DatabaseType, PolicyType, RequestStatus, RequestPriority, UserRole


# ── Company Schemas ───────────────────────────────────────

class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    industry: Optional[str] = None
    hr_name: str = Field(..., min_length=1)
    hr_email: str = Field(...)

    support_email: Optional[str] = None
    support_phone: Optional[str] = None
    support_whatsapp: Optional[str] = None
    support_message: Optional[str] = None
    login_link: Optional[str] = None


class CompanyResponse(BaseModel):
    id: str
    name: str
    industry: Optional[str]
    hr_name: str
    hr_email: str
    support_email: Optional[str]
    support_phone: Optional[str]
    support_whatsapp: Optional[str]
    support_message: Optional[str]
    login_link: Optional[str]
    is_active: bool
    schema_map: Optional[Dict[str, Any]]
    created_at: datetime

    class Config:
        from_attributes = True


class CompanySupportInfo(BaseModel):
    company_name: str
    support_email: Optional[str]
    support_phone: Optional[str]
    support_whatsapp: Optional[str]
    support_message: Optional[str]


# ── Policy Schemas ────────────────────────────────────────

class PolicyCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    policy_type: PolicyType
    content: Optional[str] = None  # For text policies


class PolicyResponse(BaseModel):
    id: str
    company_id: str
    title: str
    description: Optional[str]
    policy_type: PolicyType
    content: Optional[str]
    file_name: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PolicyUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None


# ── Database Connection Schemas ───────────────────────────

class DatabaseConnectionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    db_type: DatabaseType
    connection_config: Dict[str, Any]  # Dynamic per DB type


class DatabaseConnectionResponse(BaseModel):
    id: str
    company_id: str
    title: str
    description: Optional[str]
    db_type: DatabaseType
    schema_map: Optional[Dict[str, Any]]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class EmployeeDataUpdateRequest(BaseModel):
    employee_id: str
    updates: Dict[str, Any]


# ── Auth Schemas ──────────────────────────────────────────

class LoginRequest(BaseModel):
    mobile_number: str

    @field_validator('mobile_number')
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip() if isinstance(v, str) else v


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee_id: str
    employee_name: str
    mobile_number: str


class TokenPayload(BaseModel):
    employee_id: str
    employee_name: str
    mobile_number: str
    exp: Optional[datetime] = None


# ── Chat Schemas ──────────────────────────────────────────

class ChatMessage(BaseModel):
    message: str
    attachments: Optional[List[str]] = None


class ChatResponse(BaseModel):
    reply: str
    actions: Optional[List[Dict[str, Any]]] = None  # Interactive buttons, etc.
    notifications: Optional[List[Dict[str, Any]]] = None


# ── Approval Schemas ─────────────────────────────────────

class ApprovalRequestCreate(BaseModel):
    employee_id: str
    employee_name: Optional[str] = None
    request_type: str
    request_details: Optional[Dict[str, Any]] = None
    context: Optional[str] = None
    priority: RequestPriority = RequestPriority.NORMAL
    assigned_to_role: Optional[UserRole] = None


class ApprovalRequestResponse(BaseModel):
    id: str
    company_id: str
    employee_id: str
    employee_name: Optional[str]
    request_type: str
    request_details: Optional[Dict[str, Any]]
    context: Optional[str]
    status: RequestStatus
    priority: RequestPriority
    assigned_to_role: Optional[UserRole]
    decision_note: Optional[str]
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    summary_report: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @model_validator(mode='after')
    def extract_summary_from_details(self) -> 'ApprovalRequestResponse':
        if not self.summary_report and self.request_details:
            self.summary_report = self.request_details.get("summary_report")
        return self


class ApprovalDecision(BaseModel):
    status: RequestStatus  # approved or rejected
    decision_note: Optional[str] = None


# ── Notification Schemas ─────────────────────────────────

class NotificationResponse(BaseModel):
    id: str
    title: str
    message: str
    notification_type: str
    related_request_id: Optional[str]
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SchemaAnalysisResult(BaseModel):
    primary_key: str
    employee_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    role_column: Optional[str] = None
    categories: Dict[str, List[str]]
    master_table: Optional[str] = None
    child_tables: Optional[Dict[str, Dict[str, Any]]] = None

# ── Validated Schema Map (Pydantic enforced) ─────────────

class ValidatedSchemaMap(BaseModel):
    """Strict Pydantic model for schema_map — ensures all critical fields exist."""
    primary_key: str = Field(..., min_length=1, description="Column name for employee ID")
    employee_name: str = Field(..., min_length=1, description="Column name for employee name")
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    role_column: Optional[str] = None
    categories: Optional[Dict[str, List[str]]] = None
    master_table: Optional[str] = None
    child_tables: Optional[Dict[str, Dict[str, Any]]] = None

    @field_validator('primary_key', 'employee_name')
    @classmethod
    def must_not_be_empty(cls, v, info):
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} cannot be empty")
        return v.strip()


class VerifiedEmployeeRecord(BaseModel):
    """Validates that a fetched employee record matches the requested lookup."""
    requested_id: str
    found_id: str
    record: Dict[str, Any]
    primary_key_column: str
    is_match: bool = False

    @field_validator('is_match', mode='before')
    @classmethod
    def validate_match(cls, v, info):
        data = info.data
        req = str(data.get('requested_id', '')).strip().lower()
        found = str(data.get('found_id', '')).strip().lower()
        return req == found

    def model_post_init(self, __context):
        """After init, verify the record actually belongs to the requested employee."""
        self.is_match = (
            str(self.requested_id).strip().lower() == str(self.found_id).strip().lower()
        )
        if not self.is_match:
            raise ValueError(
                f"Data integrity violation: requested '{self.requested_id}' "
                f"but got '{self.found_id}'. Record does NOT belong to this employee."
            )
