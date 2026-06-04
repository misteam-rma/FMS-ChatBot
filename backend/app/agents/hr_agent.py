"""
Finance FMS - LangGraph Agentic Chatbot Engine
Core agent with nodes for intent understanding and sheet-backed data answers.
"""

import json
import os
import re
from functools import lru_cache
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
    workbook_context: str

    # Conversation
    messages: List[Dict[str, str]]     # Chat history
    current_input: str                 # Latest user message
    intent: str                        # Primary detected intent
    all_intents: List[str]             # All detected intents (multi-intent)
    response: str                      # Final response to send back
    actions: List[Dict[str, Any]]      # Interactive actions (buttons, etc.)

    # Data context
    employee_data: Any                 # Authenticated user's record or admin-visible rows
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


@lru_cache(maxsize=1)
def get_workbook_context() -> str:
    """Load the Finance FMS workbook description used as LLM context."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    description_path = os.path.join(repo_dir, "description.txt")
    try:
        with open(description_path, "r", encoding="utf-8") as f:
            return f.read()[:28000]
    except Exception as e:
        print(f"[AGENT CONTEXT] Failed to load description.txt: {e}")
        return ""


def get_relevant_table_names(question: str, schema_map: Dict[str, Any], role: str) -> List[str]:
    """Select Finance FMS worksheets likely needed for the current question."""
    child_tables = schema_map.get("child_tables") or {}
    available = set(child_tables.keys())
    q = question.lower()

    candidates: List[str] = []

    doer_keywords = ["doer", "deor", "door", "owner", "assignee", "assigned", "responsible"]

    keyword_map = {
        "query": ["Query_Master", "Form_Record_Responses", "Form_Reply_Responses", "Client Docs Index"],
        "reply": ["Query_Master", "Form_Reply_Responses", "Client Docs Index"],
        "report": ["Report Upload Form", "RUF Help Sheet", "NEW DASH", "NEW DASH BANK"],
        "upload": ["Report Upload Form", "RUF Help Sheet"],
        "sanction": ["Sanction Letter", "Post sanction", "FMS2", "FMS4"],
        "disbur": ["Post sanction", "FMS2", "FMS4", "Completed Dash"],
        "status": ["Status Update", "Status Dash", "Manualy Status Dash", "NEW DASH", "NEW DASH BANK"],
        "complete": ["Completed Dash"],
        "done": ["Completed Dash", "Status Dash"],
        "drop": ["Drop Dash", "Dash Help Sheet - DND"],
        "bank": ["NEW DASH BANK", "Bank & Email ID", "RAW DATA2"],
        "branch": ["NEW DASH BANK", "Bank & Email ID", "RAW DATA2"],
        "team": ["TEAM MEMBER", "TeamMatrix", "Doer Emails", "NEW DASH"],
        "doer": ["FMS1", "FMS2", "FMS3", "FMS4", "DB_Format", "Doer Emails", "TEAM MEMBER", "DATA"],
        "mail": ["Mail Log", "Doer Emails", "TEAM MEMBER"],
        "whatsapp": ["WhatsAppUsers", "ChatMessages", "Config"],
        "step": ["Steps", "StepMatrix", "Steps Directory", "NEW DASH for pc", "FMS1", "FMS2", "FMS3", "FMS4"],
        "dashboard": ["NEW DASH", "NEW DASH BANK", "NEW DASH for pc"],
        "agrasen": ["Agrasen Group"],
        "client": ["CLIENT DATA", "RAW DATA2"],
        "loan": ["RAW DATA", "RAW DATA2", "CLIENT DATA", "NEW DASH"],
        "amount": ["RAW DATA", "RAW DATA2", "CLIENT DATA", "NEW DASH"],
    }

    for keyword, tables in keyword_map.items():
        if keyword in q:
            candidates.extend(tables)

    if any(keyword in q for keyword in doer_keywords):
        candidates.extend(["FMS1", "FMS2", "FMS3", "FMS4", "DB_Format", "Doer Emails", "TEAM MEMBER", "DATA"])

    if not candidates:
        if any(token in q for token in ["client", "loan", "project", "amount", "mobile", "job code"]):
            candidates.extend(["RAW DATA2"])
        elif any(token in q for token in ["status", "pending", "done", "complete", "dropped"]):
            candidates.extend(["Status Update", "Status Dash"])
        elif role == "admin":
            candidates.extend(["RAW DATA2"])

    if role == "admin" and any(token in q for token in ["all", "total", "summary", "portfolio", "dashboard"]):
        candidates.extend(["NEW DASH", "NEW DASH BANK", "Completed Dash", "Drop Dash", "Post sanction"])

    selected = []
    for table in candidates:
        if table in available and table not in selected:
            selected.append(table)
        if len(selected) >= 6:
            break
    return selected


def filter_related_records(records: List[Dict[str, Any]], identifiers: List[str], limit: int = 25) -> List[Dict[str, Any]]:
    """Keep records that mention any identifier in any cell."""
    normalized = [i.strip().lower() for i in identifiers if i and i.strip()]
    if not normalized:
        return records[:limit]

    matches = []
    for record in records:
        values = [str(v).strip().lower() for v in record.values()]
        joined = " | ".join(values)
        if any(identifier in joined for identifier in normalized):
            matches.append(record)
            if len(matches) >= limit:
                break
    return matches


def compact_record(record: Dict[str, Any], preferred_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return a smaller record payload to reduce LLM context size."""
    preferred_fields = preferred_fields or [
        "Client Name",
        "Client Job Code",
        "Mobile Number",
        "Project Name",
        "Proposal Type",
        "Total Loan Amount",
        "Team Leader",
        "Team Engaged",
        "Concerned Person",
        "Mail Status",
        "Status",
        "Bank",
        "Branch",
    ]
    compact = {}
    lower_map = {key.lower(): key for key in record.keys()}
    for field in preferred_fields:
        key = lower_map.get(field.lower())
        if key and record.get(key) not in (None, "", []):
            compact[key] = record[key]

    if compact:
        for key, value in record.items():
            key_lower = key.lower()
            if value not in (None, "", []) and any(
                token in key_lower for token in ["doer", "planned", "actual", "status", "remark", "url", "job code"]
            ):
                compact[key] = value
        return compact

    # Fallback: keep only non-empty values and cap size.
    compact = {}
    for key, value in record.items():
        if value not in (None, "", []):
            compact[key] = value
        if len(compact) >= 12:
            break
    return compact


def extract_query_identifiers(question: str, admin_records: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Extract likely record identifiers from the query to narrow admin context."""
    identifiers: List[str] = []
    quoted = re.findall(r'"([^"]+)"', question)
    identifiers.extend([value.strip() for value in quoted if value.strip()])

    client_match = re.search(r"client\s+([a-z0-9&.,()'\\/\-\s]+)", question, re.IGNORECASE)
    if client_match:
        candidate = client_match.group(1).strip(" .?!")
        if candidate:
            identifiers.append(candidate)

    code_match = re.search(r"\b[A-Z]{2,}\d+\b", question)
    if code_match:
        identifiers.append(code_match.group(0))

    if admin_records and not identifiers:
        lowered_question = question.lower()
        for record in admin_records:
            client_name = str(record.get("Client Name", "")).strip()
            if client_name and client_name.lower() in lowered_question:
                identifiers.append(client_name)
                break

    seen = set()
    unique = []
    for value in identifiers:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


# ── Node 1: Intent Understanding ─────────────────────────

async def understand_intent(state: AgentState) -> AgentState:
    """Classify the user's intent(s) — supports multi-intent detection."""
    llm = get_llm()

    workbook_context = state.get("workbook_context") or ""

    prompt = f"""You are an intent classifier for a Finance FMS chatbot.

User: {state['employee_name']} (ID: {state['employee_id']}, Role: {state['role']})
Company: {state.get('company_name', 'Unknown')}

The user said: "{state['current_input']}"

Classify ALL intents present in this message. A single message can have MULTIPLE intents.
Available intent categories:
- "greeting" — Hello, hi, good morning, thank you, bye, small talk, etc.
- "policy_query" — Questions about process rules, workbook definitions, or how the FMS works.
- "data_query" — Questions about clients, loan files, Client Job Code, Mobile Number, bank/branch, loan amount, project name, status, steps, doers, team leader, reports, queries, sanction letter, disbursement, dashboards, completed/dropped/post-sanction files, or summaries.
- "data_update" — Admin explicitly wants to update sheet/database data.
- "leave_request" — Not normally used for Finance FMS.
- "resignation" — Not normally used for Finance FMS.
- "grievance" — Not normally used for Finance FMS.
- "approval_action" — Approval/rejection action.
- "status_check" — Checking status of a client loan file, query, report, sanction, task, or step.
- "support" — Login/access/account problems.
- "general" — Anything else: conversational chat, what can you do, broad clarification, etc.

Important:
- Finance FMS workbook context summary:
{workbook_context[:2500]}
- If the user asks about workbook data or a sheet described above, classify as "data_query".
- If the user asks what a sheet/column/step means, classify as "policy_query" or "general".
- "What can you help me with" → "general"
- If the user's Role is admin and they ask about lists, totals, dashboards, reports, or summaries, classify as "data_query".

If the message contains multiple intents, return them comma-separated.
Examples:
- "hi, show my loan file" → "greeting,data_query"
- "tell me my details and current status" → "data_query"
- "what does DB_Format do" → "policy_query"
- "thanks!" → "greeting"
- "list all post sanction files" (admin) → "data_query"

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
        f"Hello {name}! 👋 Welcome to the Finance FMS assistant. "
        f"You are logged in as **{role.title()}**. "
        f"You can ask about client loan files, bank status, queries, reports, sanction letters, "
        f"doers, dashboards, and workflow steps."
    )
    state["actions"] = []
    return state


# ── Node 3: Policy Query (RAG) ───────────────────────────

async def handle_policy_query(state: AgentState) -> AgentState:
    """Answer Finance FMS process/sheet/column definition questions from description.txt."""
    print(f"[{state['company_id']}][AGENT POLICY] Handling workbook definition query: '{state['current_input']}'")
    workbook_context = state.get("workbook_context") or ""
    if not workbook_context:
        state["response"] = "I don't have the Finance FMS workbook description loaded right now."
        state["actions"] = []
        return state

    llm = get_llm()
    prompt = f"""You are a Finance FMS workbook expert. Answer using ONLY this workbook description.

Workbook description:
{workbook_context}

Question: {state['current_input']}

Rules:
- Explain sheet purpose, columns, and connections precisely.
- If the description does not contain the answer, say so.
- Keep the answer concise and practical.
"""
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    state["response"] = response.content.strip()
    state["policy_answer"] = state["response"]
    state["actions"] = []
    print(f"[{state['company_id']}][AGENT POLICY] Workbook definition query handled successfully.")
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
        question_text = state["current_input"]

        raw_employee_data = state.get("employee_data", {})
        admin_records = raw_employee_data if isinstance(raw_employee_data, list) else None

        # Step 1: ALWAYS fetch the logged-in employee's OWN record first
        # Use the employee_data already verified by chat_router if available
        own_record = raw_employee_data if isinstance(raw_employee_data, dict) else {}
        
        if role != "admin" and not own_record:
            print(f"[{state['company_id']}][AGENT DATA QUERY] Checking adapter for direct own_record fetch...")
            db_type = DatabaseType(state.get("db_type", "google_sheets"))
            adapter = await get_adapter(db_type, state["db_config"])
            own_record = await adapter.get_record_by_key(primary_key, employee_id, table_name=validated_schema.master_table) or {}
        
        # Step 2: Pydantic verification — ensure record belongs to THIS employee
        if role != "admin" and own_record:
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

        # Fetch relevant Finance FMS worksheets for this question.
        child_tables_data = {}
        relevant_tables = get_relevant_table_names(state["current_input"], state["schema_map"], role)
        if relevant_tables:
            print(f"[{state['company_id']}][AGENT DATA QUERY] Extracting relevant Finance FMS tables: {relevant_tables}")
            db_type = DatabaseType(state.get("db_type", "google_sheets"))
            adapter = await get_adapter(db_type, state["db_config"])
            identifiers = [employee_id]
            if own_record:
                identifiers.extend([
                    str(own_record.get("Client Name", "")),
                    str(own_record.get("Mobile Number", "")),
                    str(own_record.get("Client Job Code", "")),
                ])
            if role == "admin":
                identifiers.extend(extract_query_identifiers(question_text, admin_records))

            for child_table_name in relevant_tables:
                try:
                    all_child_recs = await adapter.get_all_records(table_name=child_table_name)
                    if role == "admin":
                        selected_records = filter_related_records(all_child_recs, identifiers, limit=12)
                        if not selected_records:
                            selected_records = all_child_recs[:8]
                    else:
                        selected_records = filter_related_records(all_child_recs, identifiers, limit=10)

                    if selected_records:
                        child_tables_data[child_table_name] = {
                            "total_rows": len(all_child_recs),
                            "included_rows": [compact_record(record) for record in selected_records],
                        }
                        print(f"[{state['company_id']}][AGENT DATA QUERY] Included '{child_table_name}' with {len(selected_records)} rows.")
                except Exception as ce:
                    print(f"[{state['company_id']}][AGENT DATA QUERY] Failed to fetch child table '{child_table_name}': {ce}")

        # Step 3: Build data context based on role
        data_context = json.dumps(compact_record(own_record), indent=2, default=str) if own_record else "No data found."
        if role == "admin" and admin_records is not None and not own_record:
            query_identifiers = extract_query_identifiers(question_text, admin_records)
            narrowed_records = filter_related_records(admin_records, query_identifiers, limit=12) if query_identifiers else []
            if narrowed_records:
                data_context = json.dumps(
                    {
                        "raw_data_total_records": len(admin_records),
                        "matched_raw_data_records": [compact_record(record) for record in narrowed_records],
                    },
                    indent=2,
                    default=str,
                )
            else:
                data_context = json.dumps(
                    {
                        "raw_data_total_records": len(admin_records),
                        "raw_data_sample_records": [compact_record(record) for record in admin_records[:8]],
                    },
                    indent=2,
                    default=str,
                )
        if child_tables_data:
            data_context += f"\n\nRelated Finance FMS worksheet records:\n{json.dumps(child_tables_data, indent=2, default=str)}"
        
        # For admin/manager roles asking about other employees, fetch team data too
        extra_context = ""
        # Adding some generic keywords here to trigger team context
        team_keywords = ["team", "all", "everyone", "report", "staff", "headcount", "dashboard", "total", "portfolio", "summary"]
        user_question = question_text.lower()
        if role in ("hr", "admin", "manager") or any(kw in user_question for kw in team_keywords):
            if any(kw in user_question for kw in team_keywords):
                print(f"[{state['company_id']}][AGENT DATA QUERY] Summary/dashboard keywords detected. Pulling RAW DATA context.")
                if admin_records is None:
                    db_type = DatabaseType(state.get("db_type", "google_sheets"))
                    adapter = await get_adapter(db_type, state["db_config"])
                    master_table = validated_schema.master_table
                    admin_records = await adapter.get_all_records(table_name=master_table)
                summary_records = [compact_record(record) for record in admin_records[:15]]
                extra_context = f"\n\nAdditional RAW DATA context (you have {role} access. Total records: {len(admin_records)}):\n{json.dumps(summary_records, indent=2, default=str)}"

        # Step 4: Ask LLM to answer from verified data
        llm = get_llm()
        print(f"[{state['company_id']}][AGENT DATA QUERY] Context prepared. Sending context to AI (Length: {len(data_context) + len(extra_context)} chars).")

        # Different prompt for admin vs employee
        if role == "admin":
            data_context_label = "Available Database:"
            permission_note = "You are an ADMIN user with full access to the Finance FMS workbook records provided in context."
        else:
            data_context_label = "Authenticated user's RAW DATA record and related Finance FMS rows:"
            permission_note = "You are a regular authenticated user. Answer only from this user's RAW DATA record and related rows that match their Client Job Code, client name, or mobile number."

        workbook_context = state.get("workbook_context") or ""

        answer_prompt = f"""You are a Finance FMS assistant for {state.get('company_name', 'the company')}. Answer the question using ONLY the workbook description and sheet data below.

Access Level: {permission_note}
Logged-in User: {state['employee_name']} (ID: {state['employee_id']})
Company Name: {state.get('company_name', 'Not specified')}

Finance FMS Workbook Description:
{workbook_context}

{data_context_label}
{data_context}
{extra_context}

Question: {state['current_input']}

Rules:
- Answer ONLY from the data provided.
- Use the workbook description to understand sheet names, columns, joins, and meanings.
- For authenticated non-admin users, do not expose unrelated client records.
- If the data does not contain the answer, say "I don't have this information in the database."
- Be professional and concise.
- For numeric questions, provide calculations and summaries.
- For listing questions, show results in bullet format.
- Mention which sheet(s) your answer came from when useful.
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

    raw_employee_data = state.get("employee_data", {})
    if isinstance(raw_employee_data, list):
        emp_data = json.dumps(
            {"total_records": len(raw_employee_data), "sample_records": raw_employee_data[:20]},
            indent=2,
            default=str,
        )
    else:
        emp_data = json.dumps(raw_employee_data, indent=2, default=str)
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

    workbook_context = state.get("workbook_context") or ""

    prompt = f"""You are a helpful Finance FMS assistant for {state['employee_name']} (Role: {state['role']}) working with **{company_name}**.

Your job is to help users understand and query the Finance FMS workbook naturally and accurately.

## What you know:
- **Workbook/System:** {company_name}
- **Finance FMS workbook description:**
{workbook_context}
- **Current user/context rows:**
{emp_data}
{policy_snippet}

## How to answer:

1. **If the question is about the authenticated user's/client's loan file** → answer from the current user/context rows.

2. **If the question is about sheets, columns, workflow steps, or how the system connects** → answer from the workbook description.

3. **If the question is conversational or general** (greetings, "thank you", small talk, "how are you", "what can you do", etc.) → respond naturally and warmly. You don't need data for these.

4. **If the question asks for live sheet values that are not in the provided context** → say the specific sheet/data is not available in the provided context.

5. **If the question is completely unrelated to Finance FMS** → politely steer back to loan files, client status, banks, queries, reports, sanction letters, steps, doers, and dashboards.

## Style:
- Be warm, concise, and professional.
- Use **bold** for key facts and bullet points for lists.
- Never invent data that isn't in the workbook description or provided sheet rows.
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
        "workbook_context": get_workbook_context(),
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
