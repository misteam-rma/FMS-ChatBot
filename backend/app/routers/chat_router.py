"""
Finance FMS - Chat API Router
Authenticated users can chat about their own RAW DATA record.
Admins can query the workbook more broadly.
"""

from fastapi import APIRouter, Depends, HTTPException, status
import re
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.schemas import ChatMessage, ChatResponse, TokenPayload
from app.models.models import DatabaseConnection
from app.utils.auth import get_current_user
from app.agents.hr_agent import chat_with_agent
from app.adapters.adapter_factory import get_adapter
from app.services.company_service import get_company
from app.config import settings

router = APIRouter(prefix="/api/chat", tags=["Chat"])


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def extract_client_name_query(user_message: str) -> str | None:
    quoted_match = re.search(r'client\s+"([^"]+)"', user_message, re.IGNORECASE)
    if quoted_match:
        return quoted_match.group(1).strip()

    plain_match = re.search(r"client\s+([a-z0-9&.,()'\\/\-\s]+)", user_message, re.IGNORECASE)
    if plain_match:
        candidate = plain_match.group(1).strip(" .?!")
        if candidate:
            return candidate
    return None


def format_client_record(record: dict) -> str:
    sections = {
        "Client Information": ["Client Name", "Client Job Code", "Mobile Number", "Concerned Person"],
        "Loan Details": [
            "Project Name",
            "Proposal Type",
            "Term Loan Amt (Cr)",
            "CC Amt (Cr)",
            "BG Amt (Cr)",
            "LC Amt (Cr)",
            "OD Amt (Cr)",
            "LAP Amt (Cr)",
            "Sublimit of CC (LC/BG/WCDL) Amt (Cr)",
            "Total Loan Amount",
        ],
        "Team Details": ["Team Leader", "Team Engaged"],
        "Documents": ["Attachment URL", "Mail Status"],
    }

    reply_parts = []
    for section, fields in sections.items():
        section_data = {}
        for field in fields:
            for key, value in record.items():
                if key.lower() == field.lower() and value not in (None, ""):
                    section_data[key] = value

        if section_data:
            reply_parts.append(f"\n{section}:")
            for key, value in section_data.items():
                reply_parts.append(f"  • {key}: {value}")

    if reply_parts:
        return "\n".join(reply_parts).strip()

    return "\n".join(
        ["Client Record:"] + [f"  • {key}: {value}" for key, value in record.items() if value not in (None, "")]
    )


def format_client_list(records: list[dict], limit: int = 50) -> str:
    visible = records[:limit]
    lines = [f"Total clients in RAW DATA: {len(records)}", ""]
    for idx, rec in enumerate(visible, start=1):
        name = rec.get("Client Name") or "Unknown Client"
        code = rec.get("Client Job Code") or "N/A"
        mobile = rec.get("Mobile Number") or "N/A"
        project = rec.get("Project Name") or "N/A"
        lines.append(f"{idx}. {name} | Code: {code} | Mobile: {mobile} | Project: {project}")

    if len(records) > limit:
        lines.append("")
        lines.append(f"Showing first {limit} clients.")

    return "\n".join(lines)


@router.post("/send", response_model=ChatResponse)
async def send_message(
    data: ChatMessage,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message to the AI chatbot.
    Employees can only access their own information.
    """
    employee_id = user.employee_id
    user_type = user.user_type
    print(f"\n[CHAT LOG] 🗨️ New Chat Request from {user_type.upper()}: '{employee_id}'")

    # Fetch active company
    result = await db.execute(select(__import__('app.models.models', fromlist=['Company']).Company).where(
        __import__('app.models.models', fromlist=['Company']).Company.is_active == True
    ))
    company = result.scalars().first()

    if not company:
        print(f"[CHAT LOG] ❌ FAILED: Company not found.")
        raise HTTPException(status_code=404, detail="Company not found")

    # Fetch active database connection
    print(f"[CHAT LOG] Fetching active Database connection...")
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.company_id == company.id,
            DatabaseConnection.is_active == True,
        )
    )
    db_conn = result.scalars().first()

    if not db_conn or not db_conn.schema_map:
        print(f"[CHAT LOG] ❌ FAILED: No active Database Connection.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="System database not configured.",
        )
    print(f"[CHAT LOG] 🔗 Active DB connection found")

    schema_map = db_conn.schema_map
    master_table = schema_map.get("master_table") or settings.google_employee_sheet_name

    # Fetch authenticated user's RAW DATA record or admin context from external workbook
    employee_data = {}
    all_records = []
    try:
        adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
        primary_key = schema_map.get("primary_key", "Client Job Code")

        all_records = await adapter.get_all_records(table_name=master_table)

        # For admin: access all records
        # For employee: access only their own record
        if user_type == "admin":
            print(f"[CHAT LOG] ✅ Admin access - fetched all {len(all_records)} RAW DATA records")
        else:
            # User/client: filter to only their own RAW DATA record
            for rec in all_records:
                rec_id = str(rec.get(primary_key, "")).strip()
                if rec_id == employee_id.strip():
                    employee_data = rec
                    print(f"[CHAT LOG] ✅ Found authenticated RAW DATA record")
                    break

            if not employee_data:
                print(f"[CHAT LOG] ⚠️ WARNING: Could not find record for '{employee_id}'")

    except Exception as e:
        print(f"[CHAT LOG] Error fetching employee data: {e}")

    print(f"[CHAT LOG] Processing message directly from employee data...")
    try:
        # Simple direct response using RAW DATA (no LLM overhead)
        user_msg = data.message.lower()
        reply = None

        # Check for simple greetings (whole words only)
        if re.search(r"\b(hello|hi|hey|namaste)\b", user_msg):
            reply = f"Hello {user.employee_name}! 👋 How can I help you with your Finance FMS information today?"

        elif user_type == "admin" and re.search(r"\b(list|show)\s+all\s+(the\s+)?clients\b", user_msg):
            reply = format_client_list(all_records)

        elif user_type == "admin":
            client_name_query = extract_client_name_query(data.message)
            if client_name_query:
                normalized_query = normalize_text(client_name_query)
                matched_record = None
                for rec in all_records:
                    client_name = normalize_text(rec.get("Client Name", ""))
                    if client_name == normalized_query or normalized_query in client_name:
                        matched_record = rec
                        break

                if matched_record:
                    reply = format_client_record(matched_record)

        # Check for "tell me about me" or "my data" - ONLY for normal users, admins should use agent for any query
        elif user_type == "employee" and any(word in user_msg for word in ["tell", "show", "my data", "my details", "about me", "who am i"]):
            if employee_data:
                reply = format_client_record(employee_data)
            else:
                reply = f"I couldn't find your RAW DATA record. Your Client Job Code is {employee_id}."

        # Default: pass to agent for complex queries
        if not reply:
            print(f"[CHAT LOG] Complex query detected, passing to HR Agent...")
            # For admin: pass all records; for employee: pass only their record
            agent_context_data = all_records if user_type == "admin" else employee_data
            agent_result = await chat_with_agent(
                company_id=company.id,
                company_name=company.name,
                employee_id=employee_id,
                employee_name=user.employee_name,
                role=user_type,
                schema_map=schema_map,
                db_config=db_conn.connection_config,
                db_type=db_conn.db_type.value if db_conn else "google_sheets",
                user_message=data.message,
                employee_data=agent_context_data,
                chat_history=[],
                employee_requests=[],
            )
            reply = agent_result.get("reply", "I'm sorry, something went wrong.")

        print(f"[CHAT LOG] ✅ Chat processing completed successfully.")
    except Exception as e:
        print(f"[CHAT LOG] ❌ Chat processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Chat error: {str(e)}")

    return ChatResponse(
        reply=reply,
        actions=None,
    )
