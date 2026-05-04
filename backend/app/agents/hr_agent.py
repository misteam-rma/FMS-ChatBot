"""
Botivate HR Support - LangGraph Agentic Chatbot Engine
Core agent with nodes for Intent Understanding, Policy Search, DB Query, and Approval Routing.
"""

import json
from typing import Any, Dict, List, Optional, TypedDict, Annotated
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from app.config import settings
from app.services.rag_service import answer_from_policies
from app.adapters.adapter_factory import get_adapter
from app.models.models import DatabaseType


# ── Agent State ───────────────────────────────────────────

class AgentState(TypedDict):
    # Session info
    company_id: str
    company_name: str
    employee_id: str
    employee_name: str
    role: str
    schema_map: Dict[str, Any]
    db_config: Dict[str, Any]
    db_type: str

    # Conversation
    messages: List[Dict[str, str]]     # Chat history
    current_input: str                 # Latest user message
    intent: str                        # Primary detected intent
    all_intents: List[str]             # All detected intents (multi-intent)
    response: str                      # Final response to send back
    actions: List[Dict[str, Any]]      # Interactive actions (buttons, etc.)

    # Data context
    employee_data: Dict[str, Any]      # Full employee record
    query_result: Optional[str]        # DB query result
    policy_answer: Optional[str]       # RAG search result
    approval_needed: bool              # Whether approval workflow was triggered
    approval_request_type: Optional[str]       # Explicitly track request type (to avoid using 'greeting')
    employee_requests: Optional[List[Dict[str, Any]]] # Real DB requests for status check
    request_details: Optional[Dict[str, Any]]  # Extracted request details (dates, reason, etc.)
    sheet_sync_result: Optional[Dict[str, Any]]  # Result of Google Sheet sync operation


# ── LLM Instance ─────────────────────────────────────────

def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0.2,
    )


# ── Node 1: Intent Understanding ─────────────────────────

async def understand_intent(state: AgentState) -> AgentState:
    """Classify the user's intent(s) — supports multi-intent detection."""
    llm = get_llm()

    prompt = f"""You are an intent classifier for an HR Support chatbot.

Employee: {state['employee_name']} (ID: {state['employee_id']}, Role: {state['role']})
Company: {state.get('company_name', 'Unknown')}

The user said: "{state['current_input']}"

Classify ALL intents present in this message. A single message can have MULTIPLE intents.
Available intent categories:
- "greeting" — Hello, hi, good morning, thank you, bye, small talk, etc.
- "policy_query" — Questions about company rules, policies, leave policy, working hours, code of conduct, etc.
- "data_query" — Asking about own employment data: leave balance, salary, personal details (name, blood group, address, manager, joining date), holidays, team size, etc.
- "data_update" — HR/Admin explicitly wants to UPDATE someone's data (change designation, update salary, modify details).
- "leave_request" — Applying for leave, requesting time off (e.g. "I want leave on Monday", "apply 3 days sick leave").
- "resignation" — Submitting resignation, quitting.
- "grievance" — Filing a complaint or grievance.
- "approval_action" — Manager/HR approving or rejecting a request.
- "status_check" — Checking status of a previously submitted request.
- "support" — Password reset, login issues, account problems.
- "general" — Anything else: company info questions, conversational chat, "what can you do", "who is HR", clarifications, broad questions about the workplace, etc.

Important:
- If the user is just asking a question that doesn't clearly fit data/policy/request, prefer "general" — the general handler is smart and can answer conversationally.
- "What is my company name" → "data_query" (it's about their employment context)
- "Who is the CEO" / "Tell me about the company" → "general"
- "What can you help me with" → "general"

If the message contains multiple intents, return them comma-separated.
Examples:
- "hi, show me my leave balance" → "greeting,data_query"
- "tell me my details and company policy" → "data_query,policy_query"
- "I want to apply for leave" → "leave_request"
- "thanks!" → "greeting"
- "what is botivate" → "general"

Return ONLY the intent string(s), nothing else.
"""

    print(f"\n[{state['company_id']}][AGENT INTENT] Analyzing intent for: '{state['current_input']}'")
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    raw_intent = response.content.strip().lower().replace('"', '').replace(' ', '')
    
    # Parse multiple intents
    intents = [i.strip() for i in raw_intent.split(',') if i.strip()]
    
    # Priority list of intents (highest priority first)
    PRIORITY_ORDER = [
        "data_update",
        "leave_request",
        "resignation",
        "grievance",
        "status_check",
        "approval_action",
        "data_query",
        "policy_query",
        "support",
        "general",
        "greeting",
    ]
    
    # Sort detected intents by priority
    valid_intents = [i for i in intents if i in PRIORITY_ORDER]
    valid_intents.sort(key=lambda x: PRIORITY_ORDER.index(x))
    
    if not valid_intents:
        valid_intents = ["general"]
        
    primary_intent = valid_intents[0]
    
    # Filter out noisy secondary intents to prevent overlapping responses
    final_intents = [primary_intent]
    for intent in valid_intents[1:]:
        if intent in {"greeting", "general"}:
            continue
        if primary_intent == "status_check" and intent == "data_query":
            continue
        if primary_intent in {"data_update", "leave_request", "resignation"} and intent == "data_query":
            continue
        final_intents.append(intent)

    print(f"[{state['company_id']}][AGENT INTENT] Primary Intent: '{primary_intent}', All Intents: {final_intents}")

    # Store the primary intent for routing, and filtered intents for multi-processing
    state["intent"] = primary_intent
    state["all_intents"] = final_intents
    return state


# ── Node 2: Greeting ─────────────────────────────────────

async def handle_greeting(state: AgentState) -> AgentState:
    """Greet the user using their profile data."""
    emp = state.get("employee_data", {})
    name = state.get("employee_name", "there")
    role = state.get("role", "employee")

    print(f"[{state['company_id']}][AGENT GREETING] Triggering greeting for {name}.")

    state["response"] = (
        f"Hello {name}! 👋 Welcome to your HR Support Portal. "
        f"You are logged in as **{role.title()}**. "
        f"How can I help you today? You can ask about company policies, check your leave balance, "
        f"submit requests, or anything else related to HR."
    )
    state["actions"] = []
    return state


# ── Node 3: Policy Query (RAG) ───────────────────────────

async def handle_policy_query(state: AgentState) -> AgentState:
    """Answer policy questions using RAG or direct DB fallback. Handles both text and document policies."""
    from app.config import settings as app_settings
    print(f"[{state['company_id']}][AGENT POLICY] Handling policy query: '{state['current_input']}'")
    
    answer = None
    used_rag = False
    
    # Step 1: Try RAG if OpenAI key is available
    if app_settings.openai_api_key and app_settings.openai_api_key != "your-openai-api-key-here":
        try:
            print(f"[{state['company_id']}][AGENT POLICY] Querying RAG system...")
            answer = await answer_from_policies(state["company_id"], state["current_input"])
            # Check if RAG actually found something meaningful
            if answer and "could not find" not in answer.lower() and "no relevant" not in answer.lower():
                used_rag = True
                print(f"[{state['company_id']}][AGENT POLICY] RAG found a matching policy.")
        except Exception as e:
            print(f"[{state['company_id']}][POLICY RAG ERROR] {e}")
    
    # Step 2: If RAG didn't work, fetch policies directly from internal DB
    if not used_rag:
        print(f"[{state['company_id']}][AGENT POLICY] RAG did not answer. Falling back to direct DB fetch...")
        try:
            from app.database import async_session_factory
            from app.services.company_service import get_policies
            async with async_session_factory() as db_session:
                policies = await get_policies(db_session, state["company_id"])
                if policies:
                    policy_texts = []
                    for p in policies:
                        content = p.content or p.description or ""
                        
                        # For document-type policies, try reading file content
                        if p.policy_type and p.policy_type.value == "document" and p.file_path:
                            try:
                                if p.file_path.lower().endswith(".pdf"):
                                    from pypdf import PdfReader
                                    reader = PdfReader(p.file_path)
                                    pdf_text = ""
                                    for page in reader.pages:
                                        extracted = page.extract_text()
                                        if extracted:
                                            pdf_text += extracted + "\n"
                                    content = pdf_text.strip() if pdf_text.strip() else content
                                elif p.file_path.lower().endswith((".txt", ".md")):
                                    with open(p.file_path, "r", encoding="utf-8") as f:
                                        content = f.read()
                            except Exception as fe:
                                print(f"[{state['company_id']}][POLICY FILE READ ERROR] {fe}")
                                content = content or f"(Document: {p.file_name or p.file_path})"
                        
                        if content:
                            policy_texts.append(f"**📄 {p.title}**\n{content[:2000]}")
                        else:
                            policy_texts.append(f"**📄 {p.title}**\n{p.description or 'Policy document available. Contact HR for details.'}")
                    
                    # If we have AI, use it to format a nice answer from the policy data
                    if app_settings.openai_api_key and app_settings.openai_api_key != "your-openai-api-key-here":
                        try:
                            print(f"[{state['company_id']}][AGENT POLICY] Formatting DB fallback with LLM...")
                            llm = get_llm()
                            full_context = "\n\n---\n\n".join(policy_texts)
                            fmt_prompt = f"""You are an HR assistant. Answer the employee's question using ONLY the company policies provided below.

Company Policies:
{full_context}

Employee Question: {state['current_input']}

Answer precisely from the policies. If the specific answer isn't in the policies, summarize what policies are available.

FORMATTING RULES:
- Use clear SECTION HEADERS (###).
- Use BULLETED LISTS for readability.
- DO NOT just dump a single paragraph.
- Use DOUBLE NEWLINES between sections.
"""
                            resp = await llm.ainvoke([HumanMessage(content=fmt_prompt)])
                            answer = resp.content.strip()
                        except Exception as le:
                            print(f"[POLICY LLM FORMAT ERROR] {le}")
                            answer = "Here are your company's policies:\n\n" + "\n\n---\n\n".join(policy_texts)
                    else:
                        answer = "Here are your company's policies:\n\n" + "\n\n---\n\n".join(policy_texts)
                else:
                    answer = "No policies have been added for your company yet. Please contact your HR department."
        except Exception as e:
            print(f"[{state['company_id']}][POLICY DB FALLBACK ERROR] {e}")
            answer = "I could not retrieve policies at this time. Please try again later."
    
    state["response"] = answer or "I could not find any policy information. Please contact your HR department."
    state["policy_answer"] = state["response"]
    state["actions"] = []
    print(f"[{state['company_id']}][AGENT POLICY] Policy query handled successfully.")
    return state


# ── Node 4: Data Query (Database) ────────────────────────

async def handle_data_query(state: AgentState) -> AgentState:
    """Fetch live data from the company's database and answer.
    Uses Pydantic-verified data. Always fetches the logged-in employee's own data first.
    """
    print(f"\n[{state['company_id']}][AGENT DATA QUERY] 🟢 Starting Data Query node for user: {state['employee_name']} ({state['employee_id']})")
    print(f"[{state['company_id']}][AGENT DATA QUERY] Input Query: '{state['current_input']}'")
    
    try:
        from app.models.schemas import ValidatedSchemaMap, VerifiedEmployeeRecord
        
        # Validate schema_map using Pydantic
        try:
            validated_schema = ValidatedSchemaMap(**state["schema_map"])
            print(f"[{state['company_id']}][AGENT DATA QUERY] Schema validated successfully. Master table: {validated_schema.master_table}")
            print(f"[{state['company_id']}][AGENT DATA QUERY] Discovered Child Tables: {list(validated_schema.child_tables.keys()) if validated_schema.child_tables else 'None'}")
        except Exception as schema_err:
            print(f"[{state['company_id']}][AGENT DATA QUERY] ❌ Schema validation failed: {schema_err}")
            state["response"] = "Your company's database schema is not configured properly. Please contact HR."
            state["actions"] = []
            return state

        primary_key = validated_schema.primary_key
        employee_id = state["employee_id"]
        role = state["role"]

        # Step 1: ALWAYS fetch the logged-in employee's OWN record first
        # Use the employee_data already verified by chat_router if available
        own_record = state.get("employee_data", {})
        
        if not own_record:
            print(f"[{state['company_id']}][AGENT DATA QUERY] Checking adapter for direct own_record fetch...")
            db_type = DatabaseType(state.get("db_type", "google_sheets"))
            adapter = await get_adapter(db_type, state["db_config"])
            own_record = await adapter.get_record_by_key(primary_key, employee_id, table_name=validated_schema.master_table) or {}
        
        # Step 2: Pydantic verification — ensure record belongs to THIS employee
        if own_record:
            found_id = str(own_record.get(primary_key, ""))
            try:
                verified = VerifiedEmployeeRecord(
                    requested_id=employee_id,
                    found_id=found_id,
                    record=own_record,
                    primary_key_column=primary_key,
                )
                print(f"[{state['company_id']}][AGENT DATA QUERY] ✅ Pydantic verified own record: {employee_id} == {found_id}")
            except ValueError as ve:
                print(f"[{state['company_id']}][AGENT DATA QUERY] ❌ Pydantic verification FAILED for own record: {ve}")
                # Record doesn't match! Re-fetch with strict matching
                db_type = DatabaseType(state.get("db_type", "google_sheets"))
                adapter = await get_adapter(db_type, state["db_config"])
                master_table = validated_schema.master_table
                all_records = await adapter.get_all_records(table_name=master_table)
                own_record = None
                for r in all_records:
                    if str(r.get(primary_key, "")).strip().lower() == employee_id.strip().lower():
                        own_record = r
                        break
                if not own_record:
                    print(f"[{state['company_id']}][AGENT DATA QUERY] ❌ Employee '{employee_id}' not found in master table '{master_table}'")
                    state["response"] = f"I could not find your record in the database (Employee ID: {employee_id})."
                    state["actions"] = []
                    return state

        # Fetch child table records related to this employee or static small tables
        child_tables_data = {}
        if validated_schema.child_tables:
            print(f"[{state['company_id']}][AGENT DATA QUERY] Extracting data from child tables...")
            db_type = DatabaseType(state.get("db_type", "google_sheets"))
            adapter = await get_adapter(db_type, state["db_config"])
            for child_table_name in validated_schema.child_tables.keys():
                try:
                    all_child_recs = await adapter.get_all_records(table_name=child_table_name)
                    # Filter for records belonging to this employee
                    employee_child_recs = []
                    for r in all_child_recs:
                        # Match if employee_id is in any of the column values
                        if employee_id.lower() in [str(v).strip().lower() for v in r.values()]:
                            employee_child_recs.append(r)
                            
                    # If the table has NO records for the employee but has less than 100 rows, 
                    # it might be a shared/global table (like Holidays). Check table name / row count.
                    if not employee_child_recs and len(all_child_recs) < 50:
                        employee_child_recs = all_child_recs  # Include whole table
                        print(f"[{state['company_id']}][AGENT DATA QUERY] Included '{child_table_name}' entirely as a shared/global small table.")

                    if employee_child_recs:
                        child_tables_data[child_table_name] = employee_child_recs
                        print(f"[{state['company_id']}][AGENT DATA QUERY] Included '{child_table_name}' with {len(employee_child_recs)} records.")
                except Exception as ce:
                    print(f"[{state['company_id']}][AGENT DATA QUERY] Failed to fetch child table '{child_table_name}': {ce}")

        # Step 3: Build data context based on role
        data_context = json.dumps(own_record, indent=2, default=str) if own_record else "No data found."
        if child_tables_data:
            data_context += f"\n\nRelated Records (from other tabs):\n{json.dumps(child_tables_data, indent=2, default=str)}"
        
        # For admin/manager roles asking about other employees, fetch team data too
        extra_context = ""
        # Adding some generic keywords here to trigger team context
        team_keywords = ["team", "all employee", "everyone", "department", "report", "staff", "headcount", "dashboard", "total employee", "kitne employee"]
        user_question = state["current_input"].lower()
        if role in ("hr", "admin", "manager") or any(kw in user_question for kw in team_keywords):
            if any(kw in user_question for kw in team_keywords):
                print(f"[{state['company_id']}][AGENT DATA QUERY] Team/Dashboard keywords detected. Pulling team data context.")
                db_type = DatabaseType(state.get("db_type", "google_sheets"))
                adapter = await get_adapter(db_type, state["db_config"])
                master_table = validated_schema.master_table
                records = await adapter.get_all_records(table_name=master_table)
                extra_context = f"\n\nAdditional team data (you have {role} access. Total employees: {len(records)}):\n{json.dumps(records[:50], indent=2, default=str)}"

        # Step 4: Ask LLM to answer from verified data
        llm = get_llm()
        print(f"[{state['company_id']}][AGENT DATA QUERY] Context prepared. Sending context to AI (Length: {len(data_context) + len(extra_context)} chars).")
        
        answer_prompt = f"""You are an HR assistant at {state.get('company_name', 'the company')}. Answer the employee's question using ONLY the data below.

IMPORTANT: The logged-in employee is {state['employee_name']} (ID: {state['employee_id']}).
Company Name: {state.get('company_name', 'Not specified')}
When they ask "my details", "my data", or "my company name", ONLY use the data provided above.

Logged-in Employee's Data:
{data_context}
{extra_context}

Question: {state['current_input']}

Rules:
- Answer ONLY from the data provided.
- When asked about "my" details, ONLY use the logged-in employee's data above.
- If the data does not contain the answer, say "I don't have this information in the database."
- Be professional. 
- FORMATTING RULES: 
  - Use clear SECTION HEADERS (e.g. ### Leave Summary).
  - Use BULLETED LISTS for multiple fields.
  - DO NOT just dump a single paragraph.
  - Use bold sparingly for keys.
  - Use DOUBLE NEWLINES between sections for readability.
"""
        response = await llm.ainvoke([HumanMessage(content=answer_prompt)])
        state["response"] = response.content.strip()
        state["query_result"] = state["response"]
        print(f"[{state['company_id']}][AGENT DATA QUERY] ✅ AI Response generated.")

    except Exception as e:
        import traceback
        print(f"[{state['company_id']}][AGENT DATA QUERY ERROR] {e}\n{traceback.format_exc()}")
        state["response"] = f"I encountered an issue while fetching your data. Please try again. (Error: {str(e)})"

    state["actions"] = []
    return state


# ── Node 5: Leave Request / Grievance / Resignation ──────

async def handle_approval_request(state: AgentState) -> AgentState:
    """Handle requests that need human approval. AI NEVER approves. Extracts structured details."""
    intent = state["intent"]
    name = state["employee_name"]
    user_message = state["current_input"]

    request_type_map = {
        "leave_request": "Leave Request",
        "resignation": "Resignation",
        "grievance": "Grievance",
    }
    request_label = request_type_map.get(intent, intent.replace("_", " ").title())

    # Use AI to extract structured details from the user's message
    extracted_details = {}
    try:
        llm = get_llm()
        extract_prompt = f"""Extract structured details from this employee request message.

Employee: {name}
Request type: {request_label}
Message: "{user_message}"

Extract any of these fields if mentioned:
- leave_type (sick, casual, earned, etc.)
- start_date
- end_date
- duration (number of days — Return ONLY the NUMBER, e.g. 3)
- reason
- any other relevant details

Note: If start_date and end_date are provided but duration is missing, calculate it.
Return ONLY valid JSON with the extracted fields. If a field is not mentioned, omit it.
Example: {{"reason": "family function", "start_date": "2026-02-25", "end_date": "2026-02-27", "duration": 3, "leave_type": "casual"}}
"""
        resp = await llm.ainvoke([HumanMessage(content=extract_prompt)])
        import json, re
        raw = resp.content.strip()
        clean = re.sub(r"```json|```", "", raw).strip()
        extracted_details = json.loads(clean)
    except Exception as e:
        print(f"[DETAIL EXTRACTION ERROR] {e}")
        extracted_details = {"raw_message": user_message}

    # Store extracted details in state for the chat_router to pick up
    state["request_details"] = extracted_details

    state["response"] = (
        f"I understand you want to submit a **{request_label}**, {name}. "
        f"I have recorded your request and sent it to the appropriate authority for review. "
        f"You will be notified once a decision is made.\n\n"
        f"📋 **Request Details:**\n"
    )
    for k, v in extracted_details.items():
        if k != "raw_message":
            state["response"] += f"• **{k.replace('_', ' ').title()}**: {v}\n"

    state["response"] += (
        f"\n⚠️ Please note: I cannot approve requests myself — all approvals require human authorization."
    )
    state["approval_needed"] = True
    state["approval_request_type"] = intent  # Explicitly store the true action type
    state["actions"] = [
        {"type": "info", "text": f"📋 {request_label} submitted for approval"},
    ]
    return state


# ── Node 6: Status Check ─────────────────────────────────

async def handle_status_check(state: AgentState) -> AgentState:
    """Check the status of previous requests."""
    recent_requests = state.get("employee_requests", [])
    
    if not recent_requests:
        state["response"] = (
            f"I checked your records, {state['employee_name']}, but I couldn't find any recent requests. "
            f"If you submitted one recently, it might take a moment to appear."
        )
        state["actions"] = []
        return state
        
    # Format the recent requests directly from the real DB data
    response = f"Here is the status of your recent requests, {state['employee_name']}:\n\n"
    for req in recent_requests[:3]:  # Show top 3
        req_type = req.get("request_type", "Request").replace("_", " ").title()
        status = str(req.get("status", "pending")).title()
        date = req.get("created_at", "")[:10] if req.get("created_at") else "Recently"
        context_str = req.get("context", "")
        
        status_icon = "⏳"
        if status.lower() == "approved":
            status_icon = "✅"
        elif status.lower() == "rejected":
            status_icon = "❌"
            
        response += f"**{status_icon} {req_type}** ({date})\n"
        if context_str:
            response += f"• Details: {context_str}\n"
        response += f"• Status: **{status}**\n\n"
        
    state["response"] = response.strip()
    state["actions"] = []
    return state


# ── Node 7: Support / Password Issues ────────────────────

async def handle_support(state: AgentState) -> AgentState:
    """Redirect to company support. AI never handles password resets."""
    state["response"] = (
        f"I understand you're facing an issue, {state['employee_name']}. "
        f"Unfortunately, I cannot reset passwords or handle account issues directly.\n\n"
        f"🔒 Please contact your company's support team for help. "
        f"You can find the support contact details in the **Help** section of the app."
    )
    state["actions"] = [
        {"type": "support_card", "text": "Show Company Support Info"},
    ]
    return state


# ── Node 9: Data Update (AI-driven CRUD on Sheet) ────────

async def handle_data_update(state: AgentState) -> AgentState:
    """Handle data update requests from HR/Admin. Delegates to the DB Agent (sub-agent)."""
    role = state["role"]
    
    # Only HR, admin, manager can update data
    if role not in ("hr", "admin", "manager"):
        state["response"] = (
            f"Sorry {state['employee_name']}, you don't have permission to update employee data. "
            f"Only HR, Admin, or Manager roles can modify records. Your role: {role}."
        )
        state["actions"] = []
        return state
    
    try:
        from app.agents.db_agent import run_db_agent
        import json, re
        
        # Use AI to extract what update is being requested
        llm = get_llm()
        extract_prompt = f"""Extract the data update details from this HR/Admin request.

Employee making the request: {state['employee_name']} (Role: {role})
Message: "{state['current_input']}"

Extract:
- target_employee_id: Which employee to update (if mentioned, otherwise "self")
- updates: What fields/values to change
- reason: Why the update is needed

Return ONLY valid JSON:
{{"target_employee_id": "EMP001 or self", "updates_description": "what to update", "reason": "why"}}
"""
        resp = await llm.ainvoke([HumanMessage(content=extract_prompt)])
        raw = resp.content.strip()
        clean = re.sub(r"```json|```", "", raw).strip()
        update_details = json.loads(clean)
        
        target_id = update_details.get("target_employee_id", "self")
        if target_id == "self":
            target_id = state["employee_id"]
        
        # Build context for the DB Agent
        context = {
            "update_description": update_details.get("updates_description", state["current_input"]),
            "reason": update_details.get("reason", ""),
            "requested_by": state["employee_name"],
            "requested_by_role": role,
        }
        
        # Run the DB Agent (sub-agent)
        sync_result = await run_db_agent(
            db_type=state.get("db_type", "google_sheets"),
            connection_config=state["db_config"],
            schema_map=state["schema_map"],
            employee_id=target_id,
            action="data_update",
            context=context,
        )
        
        state["sheet_sync_result"] = sync_result
        
        if sync_result["success"]:
            updates_str = ", ".join([f"**{k}** → {v}" for k, v in sync_result["updates_applied"].items()])
            state["response"] = (
                f"✅ Database updated successfully!\n\n"
                f"**Employee:** {target_id}\n"
                f"**Updates Applied:** {updates_str}\n"
            )
            if sync_result.get("new_columns_created"):
                state["response"] += f"**New Columns Created:** {', '.join(sync_result['new_columns_created'])}\n"
            state["response"] += f"\n_Updated by {state['employee_name']} ({role})_"
        else:
            state["response"] = f"❌ Failed to update database: {sync_result['error']}"
    
    except Exception as e:
        print(f"[DATA UPDATE ERROR] {e}")
        state["response"] = f"I encountered an error while updating the database. Error: {str(e)}"
    
    state["actions"] = []
    return state


# ── Node 8: General Response ─────────────────────────────

async def handle_general(state: AgentState) -> AgentState:
    """Handle general or unclassifiable messages.

    Uses a layered approach:
    1. Pulls company policy context (RAG) when available
    2. Provides employee profile + company info
    3. Lets the LLM be a helpful general assistant that prioritizes company context
    """
    llm = get_llm()

    emp_data = json.dumps(state.get("employee_data", {}), indent=2, default=str)
    company_name = state.get("company_name") or "the company"
    user_msg = state["current_input"]

    # Try to pull a small relevant policy snippet for extra context
    policy_snippet = ""
    try:
        from app.config import settings as app_settings
        if app_settings.openai_api_key and app_settings.openai_api_key != "your-openai-api-key-here":
            from app.services.rag_service import answer_from_policies
            rag_answer = await answer_from_policies(state["company_id"], user_msg)
            if rag_answer and "could not find" not in rag_answer.lower() and "no relevant" not in rag_answer.lower():
                policy_snippet = f"\n\nRelevant company policy context:\n{rag_answer[:1500]}"
    except Exception as e:
        print(f"[{state['company_id']}][AGENT GENERAL] RAG snippet fetch skipped: {e}")

    prompt = f"""You are a helpful HR Support assistant for {state['employee_name']} (Role: {state['role']}) working at **{company_name}**.

Your job is to be genuinely helpful — answer the employee's question naturally and conversationally.

## What you know:
- **Company:** {company_name}
- **Employee profile (full record from HR database):**
{emp_data}
{policy_snippet}

## How to answer:

1. **If the question is about the employee themselves** (their name, role, salary, leave, joining date, manager, department, blood group, address, etc.) → answer directly from the employee profile above.

2. **If the question is about the company** (company name, policies, working hours, holidays, rules, etc.) → answer from the company info and policy context above.

3. **If the question is conversational or general** (greetings, "thank you", small talk, "how are you", "what can you do", etc.) → respond naturally and warmly. You don't need data for these.

4. **If the question is HR-related but you don't have the specific data** → say what info you DO have, suggest related things you can help with (policies, leave balance, request status), and offer to escalate to HR/manager if needed.

5. **If the question is completely unrelated to work** (e.g. "what's the weather", "tell me a joke", "write code") → politely steer back: "I'm focused on helping you with HR matters at {company_name} — things like policies, leave, requests, or your employment details. Is there anything HR-related I can help with?"

## Style:
- Be warm, concise, and professional.
- Use **bold** for key facts and bullet points for lists.
- Never invent data that isn't in the profile or policies.
- If you genuinely don't know, say so — but always offer a helpful next step.

The user just said: "{user_msg}"

Respond now."""
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    state["response"] = response.content.strip()
    state["actions"] = []
    return state


# ── Router Function ───────────────────────────────────────

def route_intent(state: AgentState) -> str:
    """Route to the appropriate handler node based on detected intent."""
    intent = state.get("intent", "general")
    route_map = {
        "greeting": "greeting",
        "policy_query": "policy_query",
        "data_query": "data_query",
        "data_update": "data_update",
        "leave_request": "approval_request",
        "resignation": "approval_request",
        "grievance": "approval_request",
        "approval_action": "general",
        "status_check": "status_check",
        "support": "support",
        "general": "general",
    }
    return route_map.get(intent, "general")

# ── Multi-Intent Combiner Node ───────────────────────────

INTENT_HANDLER_MAP = {
    "greeting": handle_greeting,
    "policy_query": handle_policy_query,
    "data_query": handle_data_query,
    "data_update": handle_data_update,
    "leave_request": handle_approval_request,
    "resignation": handle_approval_request,
    "grievance": handle_approval_request,
    "status_check": handle_status_check,
    "support": handle_support,
    "general": handle_general,
}

async def combine_responses(state: AgentState) -> AgentState:
    """Process any remaining intents that weren't handled by the primary route."""
    all_intents = state.get("all_intents", [])
    primary_intent = state.get("intent", "")
    primary_response = state.get("response", "")
    
    # Get secondary intents (skip the primary which was already handled)
    secondary_intents = [i for i in all_intents if i != primary_intent and i in INTENT_HANDLER_MAP]
    
    if not secondary_intents:
        return state  # Nothing extra to process
    
    # Process each secondary intent
    combined_response = primary_response
    for intent in secondary_intents:
        handler = INTENT_HANDLER_MAP.get(intent, handle_general)
        # Run the handler on a copy of the state
        temp_state = dict(state)
        temp_state["intent"] = intent
        temp_state["response"] = ""
        result_state = await handler(temp_state)
        
        secondary_response = result_state.get("response", "")
        if secondary_response:
            combined_response += f"\n\n---\n\n{secondary_response}"
        
        # Merge approval_needed flag
        if result_state.get("approval_needed"):
            state["approval_needed"] = True
            state["request_details"] = result_state.get("request_details")
            state["approval_request_type"] = result_state.get("approval_request_type")
        
        # Merge actions
        state["actions"] = (state.get("actions") or []) + (result_state.get("actions") or [])
    
    state["response"] = combined_response
    return state


# ── Build the LangGraph ──────────────────────────────────

def build_agent_graph() -> StateGraph:
    """Construct the LangGraph agent with all nodes and routing."""
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("understand_intent", understand_intent)
    graph.add_node("greeting", handle_greeting)
    graph.add_node("policy_query", handle_policy_query)
    graph.add_node("data_query", handle_data_query)
    graph.add_node("data_update", handle_data_update)
    graph.add_node("approval_request", handle_approval_request)
    graph.add_node("status_check", handle_status_check)
    graph.add_node("support", handle_support)
    graph.add_node("general", handle_general)
    graph.add_node("combine_responses", combine_responses)

    # Set entry point
    graph.set_entry_point("understand_intent")

    # Add conditional routing after intent detection
    graph.add_conditional_edges(
        "understand_intent",
        route_intent,
        {
            "greeting": "greeting",
            "policy_query": "policy_query",
            "data_query": "data_query",
            "data_update": "data_update",
            "approval_request": "approval_request",
            "status_check": "status_check",
            "support": "support",
            "general": "general",
        },
    )

    # All primary handler nodes go to combine_responses (for multi-intent)
    for node in ["greeting", "policy_query", "data_query", "data_update",
                  "approval_request", "status_check", "support", "general"]:
        graph.add_edge(node, "combine_responses")
    
    # combine_responses goes to END
    graph.add_edge("combine_responses", END)

    return graph


# Compile the graph once
agent_graph = build_agent_graph().compile()


# ── Public API ────────────────────────────────────────────

async def chat_with_agent(
    company_id: str,
    company_name: str,
    employee_id: str,
    employee_name: str,
    role: str,
    schema_map: Dict[str, Any],
    db_config: Dict[str, Any],
    db_type: str,
    user_message: str,
    employee_data: Dict[str, Any],
    chat_history: List[Dict[str, str]],
    employee_requests: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Main entry point: send a message through the agent graph.
    Returns the AI response and any interactive actions.
    """
    initial_state: AgentState = {
        "company_id": company_id,
        "company_name": company_name,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "role": role,
        "schema_map": schema_map or {},
        "db_config": db_config or {},
        "db_type": db_type or "google_sheets",
        "messages": chat_history,
        "current_input": user_message,
        "intent": "",
        "all_intents": [],
        "response": "",
        "actions": [],
        "employee_data": employee_data or {},
        "query_result": None,
        "policy_answer": None,
        "approval_needed": False,
        "approval_request_type": None,
        "request_details": None,
        "sheet_sync_result": None,
        "employee_requests": employee_requests or [],
    }

    result = await agent_graph.ainvoke(initial_state)

    return {
        "reply": result.get("response", "I'm sorry, I wasn't able to process that."),
        "actions": result.get("actions", []),
        "intent": result.get("intent", ""),
        "approval_needed": result.get("approval_needed", False),
        "approval_request_type": result.get("approval_request_type"),
        "request_details": result.get("request_details"),
    }
