"""
Botivate HR Support - Chat API Router (Simplified)
Employees can only chat about their own information.
No approval workflows, no manager features.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.schemas import ChatMessage, ChatResponse, TokenPayload
from app.models.models import DatabaseConnection
from app.utils.auth import get_current_user
from app.agents.hr_agent import chat_with_agent
from app.adapters.adapter_factory import get_adapter
from app.services.company_service import get_company

router = APIRouter(prefix="/api/chat", tags=["Chat"])


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
    print(f"\n[CHAT LOG] 🗨️ New Chat Request from Employee: '{employee_id}'")

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
    primary_key_col = schema_map.get("primary_key", "")

    # Fetch employee data from external DB
    employee_data = {}
    try:
        adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
        primary_key = schema_map.get("primary_key", "")

        all_records = await adapter.get_all_records()
        for rec in all_records:
            rec_id = str(rec.get(primary_key, "")).strip()
            if rec_id == employee_id.strip():
                employee_data = rec
                print(f"[CHAT LOG] ✅ Found employee record")
                break

        if not employee_data:
            print(f"[CHAT LOG] ⚠️ WARNING: Could not find record for '{employee_id}'")

    except Exception as e:
        print(f"[CHAT LOG] Error fetching employee data: {e}")

    print(f"[CHAT LOG] Processing message directly from employee data...")
    try:
        # Simple direct response using employee data (no LLM overhead)
        user_msg = data.message.lower()
        reply = None

        # Check for simple greetings
        if any(word in user_msg for word in ["hello", "hi", "hey", "namaste"]):
            reply = f"Hello {user.employee_name}! 👋 How can I help you with your information today?"

        # Check for "tell me about me" or "my data"
        elif any(word in user_msg for word in ["tell", "show", "my data", "my details", "about me", "who am i"]):
            if employee_data:
                # Format employee data nicely
                data_lines = ["📋 Here's your information:\n"]
                for key, value in employee_data.items():
                    if value and key.lower() not in ["employee id"]:
                        data_lines.append(f"• {key}: {value}")
                reply = "\n".join(data_lines)
            else:
                reply = f"I couldn't find your employee record. Your ID is {employee_id}."

        # Default: pass to agent for complex queries
        if not reply:
            print(f"[CHAT LOG] Complex query detected, passing to HR Agent...")
            agent_result = await chat_with_agent(
                company_id=company.id,
                company_name=company.name,
                employee_id=employee_id,
                employee_name=user.employee_name,
                role="employee",
                schema_map=schema_map,
                db_config=db_conn.connection_config,
                db_type=db_conn.db_type.value if db_conn else "google_sheets",
                user_message=data.message,
                employee_data=employee_data,
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
