"""
Finance FMS - Authentication API Router
Mobile number verification for client/user access and sheet-backed admin login.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models.schemas import LoginRequest, LoginResponse, TokenPayload
from app.models.models import Company, DatabaseConnection
from app.adapters.adapter_factory import get_adapter
from app.utils.auth import create_access_token
from app.config import settings


class AdminLoginRequest(BaseModel):
    username: str
    password: str

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def normalize_phone(value: str) -> str:
    """Normalize phone numbers for sheet matching."""
    return (
        str(value or "")
        .strip()
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .replace("+91", "")
    )


def get_first_value(record: dict, candidates: list[str], default: str = "") -> str:
    """Return the first non-empty value from a record using case-insensitive column names."""
    lower_key_map = {str(k).strip().lower(): k for k in record.keys()}
    for candidate in candidates:
        key = lower_key_map.get(candidate.strip().lower())
        if key is not None:
            value = str(record.get(key, "")).strip()
            if value:
                return value
    return default


async def get_active_connection(db: AsyncSession) -> tuple[Company, DatabaseConnection]:
    """Fetch the active company and Google Sheets connection."""
    result = await db.execute(select(Company).where(Company.is_active == True))
    company = result.scalars().first()

    if not company:
        print("[AUTH LOG] ❌ FAILED: No active company found.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="System not configured.",
        )

    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.company_id == company.id,
            DatabaseConnection.is_active == True,
        )
    )
    db_conn = result.scalars().first()

    if not db_conn or not db_conn.schema_map:
        print("[AUTH LOG] ❌ FAILED: Database not configured.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System database not configured.",
        )

    return company, db_conn


@router.post("/verify-mobile", response_model=LoginResponse)
async def verify_mobile(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Mobile number verification for employee access using Google Sheets.
    Employee can only access their own information.

    Flow:
    1. Receives mobile number from frontend
    2. Searches Google Sheet for matching phone number
    3. Returns JWT token if found
    4. Employee can now chat with AI about own data
    """
    mobile_number = data.mobile_number.strip()

    print(f"\n[AUTH LOG] 🔍 Starting Mobile Number Verification")
    print(f"[AUTH LOG] Mobile: '{mobile_number}'")

    # Step 1: Get the first active company
    print(f"[AUTH LOG] Step 1: Fetching company configuration...")
    company, db_conn = await get_active_connection(db)
    print(f"[AUTH LOG] ✅ Company: '{company.name}'")

    # Step 2: Get active database connection (Google Sheets)
    print(f"[AUTH LOG] ✅ DB Type: {db_conn.db_type}")

    # Step 3: Get adapter and schema
    try:
        adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
        schema = db_conn.schema_map
        print(f"[AUTH LOG] Step 3: Schema loaded")
    except Exception as e:
        print(f"[AUTH LOG] ❌ ERROR loading adapter: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database connection failed: {str(e)}"
        )

    # Step 4: Use RAW DATA / Mobile Number for Finance FMS authentication
    master_table = schema.get("master_table") or settings.google_employee_sheet_name
    phone_column = schema.get("phone") or "Mobile Number"

    if not phone_column:
        print(f"[AUTH LOG] ❌ Phone column not found in schema")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="System not configured for mobile verification.",
        )

    print(f"[AUTH LOG] Step 4: Phone column = '{phone_column}'")

    # Step 5: Search for employee in Google Sheet
    print(f"[AUTH LOG] Step 5: Searching '{master_table}' for mobile: '{mobile_number}'...")
    try:
        all_records = await adapter.get_all_records(table_name=master_table)
        print(f"[AUTH LOG] Total records in sheet: {len(all_records)}")

        employee = None
        for idx, record in enumerate(all_records):
            stored_mobile = str(record.get(phone_column, "")).strip()

            if normalize_phone(stored_mobile) == normalize_phone(mobile_number):
                employee = record
                print(f"[AUTH LOG] ✅ MATCH FOUND at row {idx + 2}")
                break

        if not employee:
            print(f"[AUTH LOG] ❌ No employee found with mobile: '{mobile_number}'")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Mobile number not registered in system.",
            )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH LOG] ❌ ERROR during search: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )

    # Step 6: Extract employee information
    print(f"[AUTH LOG] Step 6: Extracting employee data...")
    primary_key_col = schema.get("primary_key", "Client Job Code")
    name_col = schema.get("employee_name", "Client Name")

    employee_id = str(employee.get(primary_key_col, "")).strip()
    employee_name = str(employee.get(name_col, "User")).strip()

    if not employee_id:
        print(f"[AUTH LOG] ⚠️ Employee ID is empty, using mobile as fallback")
        employee_id = mobile_number

    print(f"[AUTH LOG] Employee ID: '{employee_id}'")
    print(f"[AUTH LOG] Employee Name: '{employee_name}'")

    # Step 7: Create JWT token
    print(f"[AUTH LOG] Step 7: Creating JWT token...")
    token_data = {
        "employee_id": employee_id,
        "employee_name": employee_name,
        "mobile_number": mobile_number,
        "user_type": "employee",
    }
    access_token = create_access_token(token_data)

    print(f"[AUTH LOG] ✅ SUCCESS: JWT token created")
    print(f"[AUTH LOG] Access granted to: {employee_name} ({employee_id})")

    return LoginResponse(
        access_token=access_token,
        employee_id=employee_id,
        employee_name=employee_name,
        mobile_number=mobile_number,
        user_type="employee",
    )


@router.post("/verify-admin", response_model=LoginResponse)
async def verify_admin(data: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Admin login backed by the Finance FMS Admin worksheet.
    Admin has full unrestricted access to the workbook.
    """
    print(f"\n[AUTH LOG] 🔐 Starting Admin Verification")
    print(f"[AUTH LOG] Username: '{data.username}'")

    company, db_conn = await get_active_connection(db)

    try:
        adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
        admin_records = await adapter.get_all_records(table_name=settings.google_admin_sheet_name)
        print(f"[AUTH LOG] Admin records loaded: {len(admin_records)}")
    except Exception as e:
        print(f"[AUTH LOG] ❌ ERROR loading Admin sheet: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Admin sheet authentication failed: {str(e)}",
        )

    username_input = str(data.username or "").strip().lower()
    password_input = str(data.password or "").strip()
    matched_admin = None

    for record in admin_records:
        username = get_first_value(
            record,
            ["Username", "User Name", "Admin Username", "Email", "Email ID", "Mobile Number", "Phone"],
        )
        password = get_first_value(
            record,
            ["Password", "Admin Password", "Passcode", "PIN", "Pin"],
        )
        is_active = get_first_value(record, ["IsActive", "Active", "Status"], "TRUE").lower()

        if not username or not password:
            continue
        if is_active in {"false", "no", "inactive", "disabled", "0"}:
            continue
        if username.strip().lower() == username_input and password == password_input:
            matched_admin = record
            break

    if not matched_admin:
        print(f"[AUTH LOG] ❌ FAILED: Invalid admin credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    print(f"[AUTH LOG] ✅ Admin credentials verified")

    # Create JWT token for admin
    print(f"[AUTH LOG] Creating JWT token for admin...")
    admin_name = get_first_value(matched_admin, ["Name", "Admin Name", "Full Name"], "Admin")
    admin_id = get_first_value(matched_admin, ["Admin ID", "ID", "User ID", "Username"], data.username)
    admin_mobile = get_first_value(matched_admin, ["Mobile Number", "Phone", "WhatsApp"], "")

    token_data = {
        "employee_id": admin_id,
        "employee_name": admin_name,
        "mobile_number": admin_mobile,
        "user_type": "admin",
    }
    access_token = create_access_token(token_data)

    print(f"[AUTH LOG] ✅ SUCCESS: Admin JWT token created")

    return LoginResponse(
        access_token=access_token,
        employee_id=admin_id,
        employee_name=admin_name,
        mobile_number=admin_mobile,
        user_type="admin",
    )
