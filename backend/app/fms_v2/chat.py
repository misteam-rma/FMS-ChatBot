"""FMS v2 chat orchestration and prompt building."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.fms_v2.admin_sources import (
    document_link_records,
    fetch_extra_admin_records,
    is_document_query,
)
from app.fms_v2.config import FMS_SHEET_NAMES, FmsSheetName
from app.fms_v2.llm import GenerateFmsAnswerInput, LlmResult, generate_fms_answer
from app.fms_v2.models import (
    FetchFmsRecordsInput,
    FetchFmsRecordsOutput,
    FmsRecord,
    FmsV2ChatMessage,
    FmsV2ChatResponse,
    SourceColumn,
    TokenPayload,
)
from app.fms_v2.sheets import (
    fetch_sheet_values_with_retry,
    normalize_client_job_code,
    parse_fms_sheet_values,
)
from app.fms_v2.tools import fetch_records_tool


logger = logging.getLogger("botivate_api.fms_v2.chat")

FetchRecordsFn = Callable[
    [FetchFmsRecordsInput],
    FetchFmsRecordsOutput | Awaitable[FetchFmsRecordsOutput],
]
GenerateAnswerFn = Callable[
    [GenerateFmsAnswerInput],
    LlmResult | Awaitable[LlmResult],
]

CLIENT_CODE_RE = re.compile(r"\b[A-Z0-9]+-F\d{2}[A-Z]-[A-Z0-9]+\b", re.IGNORECASE)
# An answer is considered grounded if it references a source. We accept any of
# the natural forms the LLM produces rather than one rigid order:
#   - a literal "Source:" label
#   - a sheet name (FMS1-FMS4) near a row and/or column reference, e.g.
#     "FMS1 row 7, column Status", "【FMS1, 7, Client Job Code】",
#     "FMS1, row 27", "(FMS2 row 12)"
# It still rejects answers that cite no sheet at all (i.e. ungrounded text).
SOURCE_CITATION_RE = re.compile(
    r"\bSource\s*:"                       # explicit label, or
    r"|\bFMS[1-4]\b[^\n]{0,60}?"          # a sheet name, followed nearby by
    r"(\brow\b|\bcol(umn)?\b|,\s*\d+)",   # a row/col word or ", <number>"
    re.IGNORECASE,
)
STATUS_TERMS = {"status", "pending", "done", "complete", "drop", "current", "stage", "step"}
DATE_TERMS = {"date", "when", "kab", "planned", "actual", "deadline", "due"}
LINK_TERMS = {"link", "url", "document", "doc", "file", "attachment", "copy"}
# A "list all codes/clients" intent: any of the list/all/count verbs combined
# with a codes/clients noun, in either order. Deliberately broad so natural
# phrasings ("give me all client job codes", "how many clients", "sabhi codes",
# "total codes", "list clients") all route to the deterministic table path
# instead of the record-capped LLM path.
_LIST_VERB = r"(list|show|tell|give|display|all|every|sab(?:hi)?|saare?|kitne|kitna|how\s*many|total|count|number\s*of)"
# The noun must clearly refer to client codes/clients, NOT a bare "code"
# (which appears in unrelated asks like "give me code for leap year").
_LIST_NOUN = (
    r"(client\s*(job\s*)?codes?|job\s*codes?|clients|companies|company|customers?)"
)
LIST_TERMS_RE = re.compile(
    rf"\b{_LIST_VERB}\b.*\b{_LIST_NOUN}\b|\b{_LIST_NOUN}\b.*\b{_LIST_VERB}\b",
    re.I | re.DOTALL,
)
# Hard ceiling on the built user message. Sized to fit the smallest provider
# input window (Groq/Cerebras ~8k tokens) so the fast providers accept the
# request instead of returning 413, while still carrying many ranked records.
PROMPT_CONTENT_CHAR_BUDGET = 28000

# Greetings / small-talk / help asks that carry no data intent. These skip the
# sheet fetch (nothing to look up) but are still answered by the LLM so the
# reply reads naturally and mirrors the user's language.
# A message counts as small-talk if it STARTS with a greeting token and stays
# short (no data intent). This catches "hi", "hello there", "namaste kaise ho",
# "good morning sir", etc., while data queries fall through to the FMS pipeline.
GREETING_START_RE = re.compile(
    r"^\s*(hi+|hey+|hello+|helo+|yo|namaste|namaskar|namste|hii+|"
    r"good\s*(morning|afternoon|evening)|gm|gn|greetings|"
    r"help|start|menu|thanks|thank\s*you|thankyou|thx|"
    r"ok|okay|kaise\s*ho|how\s*are\s*you|test)\b",
    re.IGNORECASE,
)

# Deterministic injection / abuse screen. Runs BEFORE any fetch or LLM call, so
# a malicious request is rejected instantly (and cheaply) rather than reaching
# the model. This is a fast first line of defense; the tool allowlist and
# server-side credentials remain the real least-privilege guarantee.
INJECTION_RE = re.compile(
    r"ignore\s+(all\s+)?(previous|above|prior|the)\s+(instructions|rules|prompts?)"
    r"|disregard\s+(all\s+|the\s+)?(instructions|rules|prompts?)"
    r"|(reveal|show|print|repeat|display|leak|expose)\s+(me\s+)?"
    r"(your\s+|the\s+)?(system\s+)?(prompt|instructions|rules)"
    r"|(service[\s_-]*account|api[\s_-]*key|secret|password|credential|"
    r"env(ironment)?\s+var|jwt[\s_-]*secret|\.env\b)"
    r"|you\s+are\s+now\b|pretend\s+to\s+be\b|act\s+as\s+(?!an?\s+(fms|rma|data))"
    r"|jailbreak|developer\s+mode|DAN\b",
    re.IGNORECASE,
)
# Write/mutation attempts — this is a strictly READ-ONLY assistant. A mutation
# verb followed (anywhere) by a data-object word is blocked.
MUTATION_RE = re.compile(
    r"\b(delete|remove|drop|erase|update|edit|modify|overwrite|insert|"
    r"write\s+to|set\s+the\s+value|change\s+the)\b"
    r".{0,60}\b(row|record|entry|cell|sheet|data|code|client|column|"
    r"status|marks?|amount|value|field|name|phone|number)\b",
    re.IGNORECASE,
)

INJECTION_REFUSAL = (
    "Main sirf RMA FMS loan-file queries mein madad kar sakta hoon (read-only). "
    "Aap kisi Client Job Code ka status, dates, bank ya documents pooch sakte hain."
)


def screen_user_input(message: str) -> str | None:
    """Return a refusal message if the input is an injection/abuse/write attempt,
    else None. Pure + deterministic; no network or LLM cost."""

    text = str(message or "")
    if INJECTION_RE.search(text) or MUTATION_RE.search(text):
        return INJECTION_REFUSAL
    return None
# Data-intent words: if any appear, it is NOT small-talk even if it opens with a
# greeting (e.g. "hi, status batao").
DATA_INTENT_RE = re.compile(
    r"\b(status|date|dates|planned|actual|remark|doer|bank|loan|amount|"
    r"url|link|document|doc|file|copy|project|proposal|step|"
    r"list|show|dikhao|batao|kaha|kab|kitna|client\s*code|job\s*code|"
    r"fms[1-4]?|sanction|disburs|submit|pending|complete|drop)\b",
    re.IGNORECASE,
)

# Shared language rule so every reply mirrors the user's own message.
LANGUAGE_RULES = (
    "Language (mirror the user's message):\n"
    "- If the user writes in plain English, reply ONLY in plain English.\n"
    "- If the user writes in Hinglish (Hindi words in Latin script), reply in "
    "Hinglish (Latin script).\n"
    "- If the user writes in Devanagari, reply in Devanagari Hindi.\n"
    "- Match the user's script and language every time; never switch to "
    "Hinglish when the user wrote plain English.\n"
    "- Keep Client Job Codes, sheet names, column names, bank names, and URLs "
    "exactly as provided."
)


def is_greeting_or_small_talk(message: str) -> bool:
    """True for greetings/help/small-talk that need no FMS data."""

    text = str(message or "").strip()
    if not text:
        return True
    # Any explicit Client Job Code or data-intent word means it's a real query.
    if extract_client_job_codes(text) or DATA_INTENT_RE.search(text):
        return False
    if len(text) <= 3 and not any(char.isdigit() for char in text):
        return True
    # Small-talk opens with a greeting token and stays short (<= ~8 words).
    if GREETING_START_RE.match(text) and len(text.split()) <= 8:
        return True
    return False


def build_small_talk_prompt(
    *, role: str, message: str, chat_history: Sequence[dict[str, str]] | None = None
) -> GenerateFmsAnswerInput:
    """Build a lightweight prompt for greetings/help with no FMS data."""

    system_prompt = (
        "You are the RMA FMS loan-file assistant. The user has sent a greeting "
        "or small-talk message, not a data question. Reply briefly and warmly, "
        "then invite them to ask about a Client Job Code (status, planned/actual "
        "dates, bank details, or document links). Do NOT invent any FMS data, "
        "numbers, statuses, or citations. Keep it to 1-2 short sentences.\n\n"
        f"{LANGUAGE_RULES}"
    )
    user_content = json.dumps(
        {
            "role": role,
            "user_message": message,
            "chat_history": list((chat_history or [])[-4:]),
        },
        ensure_ascii=False,
    )
    return GenerateFmsAnswerInput(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.5,
        max_tokens=200,
    )


async def chat_with_fms_v2(
    data: FmsV2ChatMessage,
    user: TokenPayload,
    *,
    fetch_records_fn: FetchRecordsFn = fetch_records_tool,
    generate_answer_fn: GenerateAnswerFn = generate_fms_answer,
    admin_records: Sequence[FmsRecord] | None = None,
) -> FmsV2ChatResponse:
    """Answer a chat message using deterministic FMS records plus the LLM."""

    started = time.perf_counter()
    validated = FmsV2ChatMessage.model_validate(data)
    role = (user.user_type or "").strip().lower()
    if role not in {"client", "admin"}:
        logger.warning("FMS v2 chat rejected unsupported role=%s", role)
        return FmsV2ChatResponse(reply="Unauthorized user role for FMS chat.")

    logger.info(
        "FMS v2 chat start role=%s employee_id=%s message_chars=%s",
        role,
        user.employee_id,
        len(validated.message),
    )

    # Deterministic input guard: reject injection / credential-probe / write
    # attempts before any fetch or LLM call (instant, no cost).
    refusal = screen_user_input(validated.message)
    if refusal is not None:
        logger.warning("FMS v2 chat input-guard blocked role=%s", role)
        return FmsV2ChatResponse(reply=refusal)

    if is_greeting_or_small_talk(validated.message):
        logger.info("FMS v2 chat small-talk role=%s (no fetch)", role)
        response = await _answer_small_talk(
            validated, role, generate_answer_fn=generate_answer_fn
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info("FMS v2 chat done role=%s small_talk=1 latency_ms=%s", role, latency_ms)
        return response

    if role == "client":
        response = await _chat_for_client(
            validated,
            user,
            fetch_records_fn=fetch_records_fn,
            generate_answer_fn=generate_answer_fn,
        )
    else:
        response = await _chat_for_admin(
            validated,
            user,
            fetch_records_fn=fetch_records_fn,
            generate_answer_fn=generate_answer_fn,
            admin_records=admin_records,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info("FMS v2 chat done role=%s latency_ms=%s", role, latency_ms)
    return response


# Last-resort reply if every LLM provider fails on a greeting. Kept minimal and
# language-neutral so we never block on a simple "hi".
SMALL_TALK_FALLBACK_REPLY = (
    "Hello! Ask me about any Client Job Code — status, planned/actual dates, "
    "bank details, or document links."
)


async def _answer_small_talk(
    data: FmsV2ChatMessage,
    role: str,
    *,
    generate_answer_fn: GenerateAnswerFn,
) -> FmsV2ChatResponse:
    """Answer greetings/small-talk via the LLM, with no fetch and no citation guard."""

    prompt = build_small_talk_prompt(
        role=role, message=data.message, chat_history=data.chat_history or []
    )
    result = await _maybe_await(generate_answer_fn(prompt))
    if not result.ok or not result.content.strip():
        logger.warning(
            "FMS v2 small-talk LLM failed provider=%s error=%s; using fallback reply",
            result.provider,
            result.error,
        )
        return FmsV2ChatResponse(reply=SMALL_TALK_FALLBACK_REPLY)
    return FmsV2ChatResponse(reply=result.content)


async def _chat_for_client(
    data: FmsV2ChatMessage,
    user: TokenPayload,
    *,
    fetch_records_fn: FetchRecordsFn,
    generate_answer_fn: GenerateAnswerFn,
) -> FmsV2ChatResponse:
    auth_code = normalize_client_job_code(user.client_job_code or user.employee_id)
    if not auth_code:
        return FmsV2ChatResponse(reply="Client Job Code login missing hai. Please login again.")

    requested_codes = extract_client_job_codes(data.message)
    if any(code != auth_code for code in requested_codes):
        return FmsV2ChatResponse(
            reply="Is Client Job Code ka data aapke login se linked nahi hai."
        )

    fetch_result = await _maybe_await(
        fetch_records_fn(FetchFmsRecordsInput(client_job_code=auth_code))
    )
    if not fetch_result.ok:
        logger.error(
            "FMS v2 client chat fetch failed client_job_code=%s errors=%s",
            auth_code,
            [error.model_dump() for error in fetch_result.errors],
        )
        return FmsV2ChatResponse(
            reply="Sheet data abhi fetch nahi ho pa raha. Please thodi der baad try karein."
        )

    if not fetch_result.records:
        return FmsV2ChatResponse(
            reply=(
                f"{auth_code} ke liye FMS1-FMS4 mein koi record nahi mila. "
                "Source check: FMS1, FMS2, FMS3, FMS4 Client Job Code columns."
            )
        )

    records = list(fetch_result.records)
    # Make the LLM a superset of the buttons: if the client asks for a document
    # / report / sanction letter, add their real download links (RUF Help Sheet
    # + Sanction Letter) so natural-language doc queries get the same links the
    # FMS button gives.
    if is_document_query(data.message):
        try:
            doc_records = await document_link_records(auth_code)
            records = doc_records + records
        except Exception:
            logger.exception("FMS v2 client doc-link fetch failed code=%s", auth_code)

    return await _answer_with_llm(
        data,
        user,
        records=records,
        generate_answer_fn=generate_answer_fn,
    )


async def _chat_for_admin(
    data: FmsV2ChatMessage,
    user: TokenPayload,
    *,
    fetch_records_fn: FetchRecordsFn,
    generate_answer_fn: GenerateAnswerFn,
    admin_records: Sequence[FmsRecord] | None,
) -> FmsV2ChatResponse:
    requested_codes = extract_client_job_codes(data.message)

    if requested_codes:
        records: list[FmsRecord] = []
        for code in requested_codes:
            result = await _maybe_await(
                fetch_records_fn(FetchFmsRecordsInput(client_job_code=code))
            )
            if result.ok:
                records.extend(result.records)
            else:
                logger.error(
                    "FMS v2 admin chat fetch failed client_job_code=%s errors=%s",
                    code,
                    [error.model_dump() for error in result.errors],
                )
        if not records:
            return FmsV2ChatResponse(
                reply=(
                    "Requested Client Job Code ke liye FMS1-FMS4 mein record nahi mila. "
                    "Source check: Client Job Code columns."
                )
            )
    else:
        if admin_records is not None:
            records = list(admin_records)
        else:
            try:
                records = await fetch_all_fms_records()
            except Exception as exc:
                logger.exception("FMS v2 admin chat fetch-all failed")
                return FmsV2ChatResponse(
                    reply=(
                        "FMS1-FMS4 sheet data abhi fetch nahi ho pa raha. "
                        f"Checked: FMS1, FMS2, FMS3, FMS4. Error: {str(exc)}"
                    )
                )

    # Admins can reach extra tabs (RAW DATA phones, dashboards) when the query
    # calls for them. Fetch these FIRST: a phone/dashboard query must win over
    # the generic "list all codes" table below.
    recent_context = " ".join(
        str(m.get("content", "")) for m in (data.chat_history or [])[-4:]
    )
    try:
        extra = await fetch_extra_admin_records(data.message, recent_context)
    except Exception:
        logger.exception("FMS v2 admin extra-source fetch failed")
        extra = []

    # The deterministic code-list table only applies when the query is purely a
    # "list the codes" ask AND no richer extra source (phone/dashboard) matched.
    if not extra and LIST_TERMS_RE.search(data.message):
        return FmsV2ChatResponse(reply=format_admin_client_list(records))

    if extra:
        # Extra records (RAW DATA phones etc.) are lean, so we can carry many —
        # important for "list N phones / all phones" queries where token-ranking
        # gives no signal. They lead the set and are never trimmed by FMS volume.
        ranked_extra = rank_records_for_question(extra, data.message, limit=40)
        ranked_fms = rank_records_for_question(list(records), data.message, limit=4)
        ranked_records = ranked_extra + ranked_fms
    else:
        ranked_records = rank_records_for_question(list(records), data.message, limit=24)

    if not ranked_records:
        return FmsV2ChatResponse(
            reply="Is query se related koi matching record nahi mila."
        )

    return await _answer_with_llm(
        data,
        user,
        records=ranked_records,
        generate_answer_fn=generate_answer_fn,
    )


async def _answer_with_llm(
    data: FmsV2ChatMessage,
    user: TokenPayload,
    *,
    records: Sequence[FmsRecord],
    generate_answer_fn: GenerateAnswerFn,
) -> FmsV2ChatResponse:
    prompt = build_fms_chat_prompt(
        role=(user.user_type or "client").lower(),
        question=data.message,
        records=records,
        authenticated_client_job_code=user.client_job_code,
        chat_history=data.chat_history or [],
    )
    result = await _maybe_await(generate_answer_fn(prompt))
    if not result.ok:
        logger.error(
            "FMS v2 LLM answer failed provider=%s model=%s error=%s",
            result.provider,
            result.model,
            result.error,
        )
        return FmsV2ChatResponse(
            reply=(
                "Sheet data fetch ho gaya tha, lekin answer generate nahi ho paya. "
                "Please retry karein."
            )
        )

    # A scope refusal ("I only help with FMS loan-file queries") makes no
    # factual claim, so it needs no Source citation — let it through.
    if not has_source_citation(result.content) and not is_scope_refusal(result.content):
        logger.warning(
            "FMS v2 LLM answer rejected because citation was missing provider=%s model=%s",
            result.provider,
            result.model,
        )
        return FmsV2ChatResponse(
            reply=(
                "Answer generate hua, lekin required source citation missing thi. "
                "Please query dobara bhejein."
            )
        )

    return FmsV2ChatResponse(reply=result.content, actions=None, notifications=None)


def build_fms_chat_prompt(
    *,
    role: str,
    question: str,
    records: Sequence[FmsRecord],
    authenticated_client_job_code: str | None,
    chat_history: Sequence[dict[str, str]] | None = None,
) -> GenerateFmsAnswerInput:
    """Build a strict, testable FMS prompt from structured records."""

    system_prompt = (
        "You are the RMA FMS loan-file assistant, and NOTHING else.\n"
        "You answer only from the provided FMS tool output about RMA's loan "
        "files — client job codes, statuses, steps, dates, banks, loan amounts, "
        "documents, and contact details found in the sheets.\n\n"
        "SCOPE (strict):\n"
        "- Answer ONLY questions about RMA FMS loan-file data present in the "
        "tool output.\n"
        "- If the user asks anything outside this scope — general knowledge, "
        "coding, math, trivia, current events, personal advice, or any topic "
        "not about RMA loan files (e.g. 'code for a leap year', 'write a poem', "
        "'what is the capital of X') — DO NOT answer it. Politely refuse in one "
        "short sentence and steer them back, e.g.: \"Main sirf RMA FMS loan-file "
        "queries mein madad kar sakta hoon. Aap kisi Client Job Code ka status, "
        "dates, bank ya documents pooch sakte hain.\" A refusal needs no Source "
        "citation.\n"
        "- Never invent capabilities, run code, or answer hypotheticals.\n\n"
        "Never fabricate missing values. Never use a value unless it appears in "
        "the tool output. For each factual claim, cite the source in this exact "
        "format: `Source: <sheet> row <row_number>, column <column_name>` "
        "(example: `Source: FMS1 row 7, column Status`). You may cite multiple "
        "values, but every factual answer MUST contain at least one such Source "
        "line. If data is missing, say it is missing and mention which "
        "sheet/columns were checked.\n\n"
        f"{LANGUAGE_RULES}\n\n"
        "Authorization:\n"
        "- If role is client, answer only for the authenticated Client Job Code.\n"
        "- If role is admin, answer from any sheet present in the tool output "
        "(FMS1-FMS4, and when included, RAW DATA, NEW DASH, Completed Dash). "
        "RAW DATA holds client contact details such as Mobile Number.\n\n"
        "Reasoning:\n"
        "- Use exact Client Job Code matches.\n"
        "- FMS workflow columns repeat by step context, such as Doer, Planned, "
        "Actual, URL, Remark, and Status.\n"
        "- For status queries, check Status, Doer, Planned, Actual, URL, Remark, "
        "and relevant step context before answering.\n"
        "- For date queries, clearly separate Planned and Actual.\n"
        "- For links, output Markdown links only when the real URL is present.\n"
        "- Avoid over-answering if the query asks for one field."
    )

    # Report the sheets actually present so the LLM cites the real source.
    present_sheets = list(dict.fromkeys(r.sheet_name for r in records)) or list(FMS_SHEET_NAMES)

    def build_payload(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "role": role,
            "authenticated_client_job_code": authenticated_client_job_code,
            "question": question,
            "chat_history": list((chat_history or [])[-6:]),
            "schema_notes": {
                "sheets": present_sheets,
                "workflow_columns": ["Doer", "Planned", "Actual", "URL", "Remark", "Status"],
                "citation_required": "Cite sheet_name, row_number, and column_name for every value.",
            },
            "tool_results": tool_results,
        }

    # `records` is already ranked/capped by the caller; don't re-cap here — the
    # char-budget loop below is the real size limiter.
    tool_results = records_to_prompt_payload(question, records, max_records=len(records) or 1)
    # FMS rows are extremely wide (166-209 columns), so a broad admin query can
    # serialize past the LLM message limit. Trim records until the built user
    # message fits the character budget, keeping the highest-ranked records.
    prefix = (
        "Use this deterministic FMS tool output to answer the user's question. "
        "Do not assume anything outside this JSON.\n\n"
    )
    while tool_results:
        payload = build_payload(tool_results)
        user_content = prefix + json.dumps(payload, ensure_ascii=False, indent=2)
        if len(user_content) <= PROMPT_CONTENT_CHAR_BUDGET:
            break
        # Drop the lowest-ranked record and retry.
        tool_results = tool_results[:-1]
    else:
        user_content = prefix + json.dumps(build_payload([]), ensure_ascii=False, indent=2)

    if not tool_results:
        logger.warning(
            "FMS v2 prompt trimmed to zero records under char budget; "
            "records may be too wide to serialize."
        )
    return GenerateFmsAnswerInput(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_tokens=1400,
    )


def records_to_prompt_payload(
    question: str,
    records: Sequence[FmsRecord],
    *,
    max_records: int = 10,
    max_fields_per_record: int = 30,
) -> list[dict[str, Any]]:
    """Serialize FMS records into compact structured context with sources."""

    ranked = rank_records_for_question(records, question, limit=max_records)
    return [
        record_to_prompt_payload(record, question, max_fields=max_fields_per_record)
        for record in ranked
    ]


def record_to_prompt_payload(
    record: FmsRecord,
    question: str,
    *,
    max_fields: int = 70,
) -> dict[str, Any]:
    fields = {**record.base_fields, **record.step_fields}
    selected_names = select_field_names_for_question(question, fields, max_fields=max_fields)
    selected_fields = {
        name: truncate_prompt_value(fields[name]) for name in selected_names if name in fields
    }
    sources = {
        name: source_to_prompt(record.source_columns[name])
        for name in selected_fields
        if name in record.source_columns
    }

    return {
        "sheet_name": record.sheet_name,
        "row_number": record.row_number,
        "client_job_code": record.client_job_code,
        "client_name": record.client_name,
        "fields": selected_fields,
        "sources": sources,
    }


def truncate_prompt_value(value: Any, *, max_chars: int = 500) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated]"


def select_field_names_for_question(
    question: str,
    fields: dict[str, Any],
    *,
    max_fields: int,
) -> list[str]:
    normalized_question = question.lower()
    query_tokens = tokenize(question)
    priority_names: list[str] = []

    for name in fields:
        normalized_name = name.lower()
        if normalized_name in {
            "client job code",
            "client name",
            "project name",
            "name of project",
            "proposal type",
            "total loan amount",
            "bank name & branch name",
        }:
            priority_names.append(name)
        elif STATUS_TERMS & query_tokens and any(
            term in normalized_name for term in ["status", "doer", "planned", "actual", "remark"]
        ):
            priority_names.append(name)
        elif DATE_TERMS & query_tokens and any(
            term in normalized_name for term in ["planned", "actual", "date"]
        ):
            priority_names.append(name)
        elif LINK_TERMS & query_tokens and any(
            term in normalized_name for term in ["url", "link", "attachment", "copy", "document"]
        ):
            priority_names.append(name)
        elif any(token and token in normalized_name for token in query_tokens):
            priority_names.append(name)

    for name in fields:
        if name not in priority_names and any(
            token and token in str(fields[name]).lower() for token in query_tokens
        ):
            priority_names.append(name)

    for name in fields:
        if name not in priority_names:
            priority_names.append(name)

    if STATUS_TERMS & query_tokens and "status" not in normalized_question:
        # Status-like Hinglish queries often say "kaha tak" without the word
        # status. Keep status/workflow fields high in the context anyway.
        priority_names.sort(
            key=lambda item: 0
            if any(term in item.lower() for term in ["status", "actual", "planned", "remark"])
            else 1
        )

    return priority_names[:max_fields]


def rank_records_for_question(
    records: Sequence[FmsRecord],
    question: str,
    *,
    limit: int,
) -> list[FmsRecord]:
    if len(records) <= limit:
        return list(records)

    query_tokens = tokenize(question)
    scored: list[tuple[int, int, FmsRecord]] = []
    for index, record in enumerate(records):
        haystack = " ".join(
            [
                record.sheet_name,
                record.client_job_code,
                record.client_name,
                *[str(value) for value in record.base_fields.values()],
                *[str(value) for value in record.step_fields.values()],
            ]
        ).lower()
        score = sum(1 for token in query_tokens if token in haystack)
        if record.client_job_code and record.client_job_code.lower() in question.lower():
            score += 10
        scored.append((score, -index, record))

    scored.sort(reverse=True)
    # Keep the top `limit` records by score, preserving insertion order for ties
    # and zero-score records. Previously a `score > 0` filter dropped every
    # zero-score record, which collapsed broad "list all phones" queries (whose
    # keywords match column names, not row values) down to a single row.
    return [record for _score, _index, record in scored[:limit]]


async def fetch_all_fms_records(
    *,
    timeout_seconds: float = 10,
    attempts: int = 3,
) -> list[FmsRecord]:
    """Fetch and parse every FMS1-FMS4 row for admin-only narrowing."""

    records: list[FmsRecord] = []
    for sheet_name in FMS_SHEET_NAMES:
        values = await fetch_sheet_values_with_retry(
            sheet_name,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
        )
        records.extend(parse_fms_sheet_values(sheet_name, values))
    return records


def format_admin_client_list(records: Sequence[FmsRecord], *, limit: int = 80) -> str:
    """Render the Client Job Code list as a GFM markdown table with a caption.

    The frontend renders GFM markdown, so a valid table needs a header row and a
    `|---|` separator line. Any literal '|' in a value is escaped so it does not
    break the table layout.
    """

    grouped: dict[str, list[FmsRecord]] = defaultdict(list)
    for record in records:
        grouped[record.client_job_code].append(record)

    lines = [
        f"**Unique Client Job Codes: {len(grouped)}**",
        "",
        "| # | Client Job Code | Client Name | Rows | Source |",
        "|---|---|---|---:|---|",
    ]
    for index, (code, code_records) in enumerate(sorted(grouped.items())[:limit], start=1):
        first = code_records[0]
        client = first.client_name or first.base_fields.get("Client Name") or "N/A"
        source = first.source_columns.get("Client Job Code")
        source_text = (
            f"{source.sheet_name} row {source.row_number}, col {source.column_name}"
            if source
            else f"{first.sheet_name} row {first.row_number}"
        )
        lines.append(
            f"| {index} | {_md_cell(code)} | {_md_cell(client)} | "
            f"{len(code_records)} | {_md_cell(source_text)} |"
        )

    if len(grouped) > limit:
        lines.append("")
        lines.append(f"_Showing first {limit} of {len(grouped)} Client Job Codes._")

    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    """Escape a value for safe use inside a markdown table cell."""

    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def extract_client_job_codes(text: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for match in CLIENT_CODE_RE.findall(str(text or "").upper()):
        code = normalize_client_job_code(match)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def has_source_citation(text: str) -> bool:
    return bool(SOURCE_CITATION_RE.search(str(text or "")))


# A short, no-data reply that declines an out-of-scope question. Such replies
# make no factual claim, so the citation guard must not reject them.
_REFUSAL_RE = re.compile(
    r"(sirf\s+rma|only\s+(help|assist).{0,30}(fms|loan)|"
    r"out\s+of\s+scope|cannot\s+(help|answer).{0,30}(that|this)|"
    r"madad\s+kar\s+sakta|is\s+topic\s+(par|mein)\s+madad|"
    r"loan-file\s+quer)",
    re.IGNORECASE,
)


def is_scope_refusal(text: str) -> bool:
    """True for a short scope-refusal reply (no factual claim, no citation needed)."""

    t = str(text or "").strip()
    # Refusals are short; a long answer that happens to match is likely a real
    # answer missing its citation, so keep the length guard.
    return len(t) <= 400 and bool(_REFUSAL_RE.search(t))


def tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9]+", str(text or "").lower())
        if len(token) >= 3
    }


def source_to_prompt(source: SourceColumn) -> dict[str, Any]:
    return {
        "sheet": source.sheet_name,
        "row": source.row_number,
        "column": source.column_name,
        "column_letter": source.column_letter,
    }


async def _maybe_await(value):
    if isinstance(value, Awaitable):
        return await value
    return value
