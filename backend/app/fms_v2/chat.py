"""FMS v2 chat orchestration and prompt building."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

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
SOURCE_CITATION_RE = re.compile(
    r"\bFMS[1-4]\b.*\brow\s+\d+\b.*\bcolumn\b|\bSource\s*:",
    re.IGNORECASE | re.DOTALL,
)
STATUS_TERMS = {"status", "pending", "done", "complete", "drop", "current", "stage", "step"}
DATE_TERMS = {"date", "when", "kab", "planned", "actual", "deadline", "due"}
LINK_TERMS = {"link", "url", "document", "doc", "file", "attachment", "copy"}
LIST_TERMS_RE = re.compile(
    r"\b(list|show|tell|dikhao|batao)\b.*\b(clients?|codes?|job codes?)\b",
    re.I,
)
# Hard ceiling on the built user message. Stays well under the LlmMessage 80k
# limit and leaves room for the system prompt and the model's context window.
PROMPT_CONTENT_CHAR_BUDGET = 60000


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

    return await _answer_with_llm(
        data,
        user,
        records=fetch_result.records,
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

    if LIST_TERMS_RE.search(data.message):
        return FmsV2ChatResponse(reply=format_admin_client_list(records))

    ranked_records = rank_records_for_question(records, data.message, limit=24)
    if not ranked_records:
        return FmsV2ChatResponse(
            reply="FMS1-FMS4 mein is query se related koi matching record nahi mila."
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

    if not has_source_citation(result.content):
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
        "You are an FMS loan-file assistant for RMA.\n"
        "You answer only from the provided FMS tool output. The output contains "
        "rows from FMS1, FMS2, FMS3, and FMS4.\n\n"
        "Never fabricate missing values. Never use a value unless it appears in "
        "the tool output. For each factual claim, cite sheet, row number, and "
        "column name. If data is missing, say it is missing and mention which "
        "sheet/columns were checked.\n\n"
        "Language:\n"
        "- Default to Hinglish in Latin script.\n"
        "- If the user writes plain English, reply in simple English.\n"
        "- If the user writes Devanagari, reply in Devanagari Hindi.\n"
        "- Keep Client Job Codes, sheet names, column names, bank names, and URLs "
        "exactly as provided.\n\n"
        "Authorization:\n"
        "- If role is client, answer only for the authenticated Client Job Code.\n"
        "- If role is admin, answer across FMS1-FMS4 only.\n\n"
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

    def build_payload(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "role": role,
            "authenticated_client_job_code": authenticated_client_job_code,
            "question": question,
            "chat_history": list((chat_history or [])[-6:]),
            "schema_notes": {
                "sheets": list(FMS_SHEET_NAMES),
                "workflow_columns": ["Doer", "Planned", "Actual", "URL", "Remark", "Status"],
                "citation_required": "Cite sheet_name, row_number, and column_name for every value.",
            },
            "tool_results": tool_results,
        }

    tool_results = records_to_prompt_payload(question, records)
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
    return [record for score, _index, record in scored[:limit] if score > 0] or [
        record for _score, _index, record in scored[:limit]
    ]


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
    grouped: dict[str, list[FmsRecord]] = defaultdict(list)
    for record in records:
        grouped[record.client_job_code].append(record)

    lines = [f"Unique Client Job Codes: {len(grouped)}", ""]
    for index, (code, code_records) in enumerate(sorted(grouped.items())[:limit], start=1):
        first = code_records[0]
        client = first.client_name or first.base_fields.get("Client Name") or "N/A"
        source = first.source_columns.get("Client Job Code")
        citation = (
            f"Source: {source.sheet_name} row {source.row_number}, column {source.column_name}."
            if source
            else f"Source: {first.sheet_name} row {first.row_number}."
        )
        lines.append(f"{index}. {code} | {client} | Rows: {len(code_records)}. {citation}")

    if len(grouped) > limit:
        lines.append("")
        lines.append(f"Showing first {limit} of {len(grouped)} Client Job Codes.")

    return "\n".join(lines)


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
