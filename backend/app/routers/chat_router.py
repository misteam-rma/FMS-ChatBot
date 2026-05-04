"""
Botivate HR Support - Chat API Router
Connects the frontend chatbot to the LangGraph agent.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.schemas import ChatMessage, ChatResponse, TokenPayload, ApprovalRequestCreate
from app.models.models import DatabaseConnection, RequestPriority, UserRole
from app.utils.auth import get_current_user
from app.agents.hr_agent import chat_with_agent
from app.adapters.adapter_factory import get_adapter
from app.services.company_service import get_company
from app.services.approval_service import create_approval_request

router = APIRouter(prefix="/api/chat", tags=["Chat"])


@router.post("/send", response_model=ChatResponse)
async def send_message(
    data: ChatMessage,
    user: TokenPayload = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message to the AI chatbot.
    The agent handles intent detection, RAG, DB queries, and approval routing.
    """
    employee_id = user.employee_id
    company_id = user.company_id
    print(f"\n[{company_id}][CHAT LOG] 🗨️ New Chat Request from Employee: '{employee_id}'")

    # Fetch company 
    company = await get_company(db, company_id)
    if not company:
        print(f"[{company_id}][CHAT LOG] ❌ FAILED: Company not found.")
        raise HTTPException(status_code=404, detail="Company not found")

    # Fetch active database connection
    print(f"[{company_id}][CHAT LOG] Fetching active Database connection for the chat context...")
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.company_id == company_id,
            DatabaseConnection.is_active == True,
        )
    )
    db_conn = result.scalars().first()
    if not db_conn or not db_conn.schema_map:
        print(f"[{company_id}][CHAT LOG] ❌ FAILED: No active Database Connection or schema found for company.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Company database not configured properly.",
        )
    print(f"[{company_id}][CHAT LOG] 🔗 Active DB connection found (Type: {db_conn.db_type})")

    # Build context for the AI
    schema_map = db_conn.schema_map
    primary_key_col = schema_map.get("primary_key", "")
    print(f"[{company_id}][CHAT LOG] DB Schema Primary Key is mapped to: '{primary_key_col}'")

    # Fetch employee data from external DB with Pydantic verification
    employee_data = {}
    if db_conn and schema_map:
        try:
            from app.models.schemas import VerifiedEmployeeRecord
            adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
            primary_key = schema_map.get("primary_key", "")
            master_table = schema_map.get("master_table", None)

            # Auto-validate schema: re-analyze if primary_key not in actual headers OR if multi-table structure is missing
            needs_reanalysis = False
            if master_table is None or "child_tables" not in schema_map:
                needs_reanalysis = True
                print(f"[CHAT] Legacy schema detected (missing master_table/child_tables). Forcing re-analysis for multi-tab support...")
            else:
                actual_headers = await adapter.get_headers(table_name=master_table)
                if primary_key and primary_key not in actual_headers:
                    needs_reanalysis = True
                    print(f"[CHAT] Schema stale! primary_key '{primary_key}' not in headers of {master_table}. Re-analyzing...")

            if needs_reanalysis:
                from app.services.schema_analyzer import analyze_schema
                
                # Fetch all tables for re-analysis
                available_tables = await adapter.get_available_tables()
                tables_headers = {}
                for tb in available_tables:
                    tables_headers[tb] = await adapter.get_headers(table_name=tb)
                    
                new_schema = await analyze_schema(tables_headers)
                schema_map = new_schema.model_dump()
                db_conn.schema_map = schema_map
                await db.commit()
                primary_key = schema_map.get("primary_key", "")
                master_table = schema_map.get("master_table", None)
                print(f"[CHAT] Re-analyzed. New primary_key: '{primary_key}', master_table: '{master_table}'")

            print(f"[CHAT] Fetching employee '{user.employee_id}' using primary_key '{primary_key}' in '{master_table}'")
            raw_record = await adapter.get_record_by_key(primary_key, user.employee_id, table_name=master_table)
            
            # Pydantic verification: ensure fetched record belongs to the logged-in user
            if raw_record:
                found_id = str(raw_record.get(primary_key, ""))
                try:
                    verified = VerifiedEmployeeRecord(
                        requested_id=user.employee_id,
                        found_id=found_id,
                        record=raw_record,
                        primary_key_column=primary_key,
                    )
                    employee_data = verified.record
                    print(f"[CHAT] ✅ Pydantic verified: {user.employee_id} == {found_id}")
                except ValueError as ve:
                    print(f"[CHAT] ❌ Pydantic verification FAILED: {ve}")
                    # Strict fallback: manually search all records
                    all_recs = await adapter.get_all_records(table_name=master_table)
                    for r in all_recs:
                        if str(r.get(primary_key, "")).strip().lower() == user.employee_id.strip().lower():
                            employee_data = r
                            print(f"[CHAT] ✅ Found correct record via manual search")
                            break
            else:
                print(f"[CHAT] ⚠️ No record found for '{user.employee_id}'")
        except Exception as e:
            print(f"[CHAT] Error fetching employee data: {e}")

    # Fetch recent requests so the agent can accurately answer status_check intents
    from app.services.approval_service import get_employee_requests
    recent_requests_orm = await get_employee_requests(db, user.company_id, user.employee_id)
    recent_requests = [
        {
            "id": r.id,
            "request_type": r.request_type,
            "status": r.status.value,
            "context": r.context,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in recent_requests_orm
    ]

    # Pre-fetch the employee's entire row using the ID from the token (validated)
    adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
    print(f"[{company_id}][CHAT LOG] Fetching comprehensive row record for Employee ID '{employee_id}'...")
    master_table = schema_map.get("master_table", None) if schema_map else None
    employee_record = await adapter.get_record_by_key(primary_key_col, employee_id, table_name=master_table)
    
    if not employee_record:
       print(f"[{company_id}][CHAT LOG] ⚠️ WARNING: Could not find exact direct match for '{employee_id}' across column '{primary_key_col}'. Applying fallback matching (Case-Insensitive)...")
       all_records = await adapter.get_all_records(table_name=master_table)
       for rec in all_records:
           rec_val = str(rec.get(primary_key_col, "")).strip().lower()
           if rec_val == employee_id.strip().lower():
               employee_record = rec
               print(f"[{company_id}][CHAT LOG] ✅ Fallback successful. Found a case-insensitive match: '{rec_val}'.")
               break
       if not employee_record:
           print(f"[{company_id}][CHAT LOG] ❌ FAILED: Found NO MATCH AT ALL using fallback.")
           employee_record = {}

    print(f"[{company_id}][CHAT LOG] Passing control to primary HR LangGraph Agent with user message: '{data.message}'")
    # Send message to LangGraph agent
    try:
        agent_result = await chat_with_agent(
            company_id=company_id,
            company_name=company.name,
            employee_id=employee_id,
            employee_name=user.employee_name,
            role=user.role.value if isinstance(user.role, UserRole) else user.role,
            schema_map=schema_map,
            db_config=db_conn.connection_config,
            db_type=db_conn.db_type.value if db_conn else "google_sheets",
            user_message=data.message,
            employee_data=employee_record,
            chat_history=[],
            employee_requests=recent_requests,
        )
        print(f"[{company_id}][CHAT LOG] ✅ Agent processing completed successfully. Returning ChatResponse.")
    except Exception as e:
        print(f"[{company_id}][CHAT ERROR] ❌ Primary LangGraph Agent crashed: {e}")
        raise HTTPException(status_code=500, detail=f"Agent runtime error: {str(e)}")

    # If approval is needed, create the request in DB
    if agent_result.get("approval_needed"):
        # ALWAYS use the specific approval request type, not the general intent
        intent = agent_result.get("approval_request_type") or agent_result.get("intent", "general")
        request_details = agent_result.get("request_details") or {}
        try:
            await create_approval_request(
                db=db,
                company_id=user.company_id,
                data=ApprovalRequestCreate(
                    employee_id=user.employee_id,
                    employee_name=user.employee_name,
                    request_type=intent,
                    request_details=request_details,
                    context=data.message,
                    priority=RequestPriority.NORMAL,
                    assigned_to_role=UserRole.MANAGER,
                ),
            )
        except Exception as e:
            print(f"[CHAT] Error creating approval request: {e}")

    return ChatResponse(
        reply=agent_result.get("reply", "I'm sorry, something went wrong."),
        actions=agent_result.get("actions"),
    )
