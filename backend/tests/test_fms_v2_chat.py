"""Tests for FMS v2 chat orchestration and prompt building."""

import pytest

from app.fms_v2.chat import (
    build_fms_chat_prompt,
    chat_with_fms_v2,
    extract_client_job_codes,
    format_admin_client_list,
    has_source_citation,
    records_to_prompt_payload,
)
from app.fms_v2.llm import LlmResult
from app.fms_v2.models import (
    FetchFmsRecordsInput,
    FetchFmsRecordsOutput,
    FmsRecord,
    FmsV2ChatMessage,
    SourceColumn,
    TokenPayload,
)


def source(sheet="FMS1", row=7, column="Status", letter="K"):
    return SourceColumn(
        sheet_name=sheet,
        row_number=row,
        column_name=column,
        column_letter=letter,
    )


def sample_record(code="HOACPL-F25F-TL01", sheet="FMS1", row=7):
    return FmsRecord(
        sheet_name=sheet,
        row_number=row,
        client_job_code=code,
        client_name="Hindustan Oil",
        base_fields={
            "Client Job Code": code,
            "Client Name": "Hindustan Oil",
            "Project Name": "Expansion",
            "Total Loan Amount": "10",
        },
        step_fields={
            "Status": "Pending",
            "Step 1 - Doer": "Asha",
            "Step 1 - Planned": "2026-01-10",
            "Step 1 - Actual": "",
            "Step 1 - Remark": "Documents awaited",
        },
        source_columns={
            "Client Job Code": source(sheet, row, "Client Job Code", "J"),
            "Client Name": source(sheet, row, "Client Name", "B"),
            "Project Name": source(sheet, row, "Project Name", "C"),
            "Total Loan Amount": source(sheet, row, "Total Loan Amount", "H"),
            "Status": source(sheet, row, "Status", "K"),
            "Step 1 - Doer": source(sheet, row, "Step 1 - Doer", "L"),
            "Step 1 - Planned": source(sheet, row, "Step 1 - Planned", "M"),
            "Step 1 - Actual": source(sheet, row, "Step 1 - Actual", "N"),
            "Step 1 - Remark": source(sheet, row, "Step 1 - Remark", "O"),
        },
    )


def client_user(code="HOACPL-F25F-TL01"):
    return TokenPayload(
        employee_id=code,
        employee_name="Hindustan Oil",
        mobile_number="",
        user_type="client",
        client_job_code=code,
    )


def admin_user():
    return TokenPayload(
        employee_id="admin",
        employee_name="Admin",
        mobile_number="",
        user_type="admin",
        client_job_code=None,
    )


def test_extract_client_job_codes_normalizes_unique_codes():
    assert extract_client_job_codes(
        "Check hoacpl-f25f-tl01 and HOACPL-F25F-TL01 plus ITPL-F25E-SUBCC02"
    ) == ["HOACPL-F25F-TL01", "ITPL-F25E-SUBCC02"]


def test_source_citation_detector_requires_sheet_row_column_or_source_label():
    assert has_source_citation("Status pending. Source: FMS1 row 7, column Status.")
    assert has_source_citation("Status pending hai. FMS2 row 12, column Remark.")
    assert not has_source_citation("Status pending hai.")


def test_prompt_builder_includes_strict_rules_and_structured_sources():
    prompt = build_fms_chat_prompt(
        role="client",
        question="status kya hai?",
        records=[sample_record()],
        authenticated_client_job_code="HOACPL-F25F-TL01",
        chat_history=[{"role": "user", "content": "old question"}],
    )

    assert prompt.messages[0].role == "system"
    assert "Never fabricate missing values" in prompt.messages[0].content
    # Language must mirror the user, not default to Hinglish.
    assert "mirror the user's message" in prompt.messages[0].content
    assert "reply ONLY in plain English" in prompt.messages[0].content
    data_message = prompt.messages[1].content
    assert '"sheet_name": "FMS1"' in data_message
    assert '"row_number": 7' in data_message
    assert '"Status": "Pending"' in data_message
    assert '"column": "Status"' in data_message


@pytest.mark.asyncio
async def test_client_chat_refuses_other_client_job_code_without_fetching():
    async def fake_fetch(_data):
        raise AssertionError("fetch should not be called")

    async def fake_generate(_data):
        raise AssertionError("llm should not be called")

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="ITPL-F25E-SUBCC02 ka status batao"),
        client_user(),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )

    assert response.reply == "Is Client Job Code ka data aapke login se linked nahi hai."


@pytest.mark.asyncio
async def test_client_chat_fetches_authenticated_code_and_calls_llm():
    calls = {}

    async def fake_fetch(data: FetchFmsRecordsInput):
        calls["fetch_code"] = data.client_job_code
        return FetchFmsRecordsOutput(
            ok=True,
            client_job_code=data.client_job_code,
            records=[sample_record()],
            errors=[],
            latency_ms=1,
        )

    async def fake_generate(prompt):
        calls["prompt"] = prompt
        return LlmResult(
            ok=True,
            provider="groq",
            model="gpt-oss-120b",
            content="Status Pending hai. Source: FMS1 row 7, column Status.",
            latency_ms=2,
        )

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="Mera current status kya hai?"),
        client_user(),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )

    assert calls["fetch_code"] == "HOACPL-F25F-TL01"
    assert response.reply.startswith("Status Pending")
    assert "FMS1" in calls["prompt"].messages[1].content


@pytest.mark.asyncio
async def test_client_chat_rejects_uncited_llm_answer():
    async def fake_fetch(data: FetchFmsRecordsInput):
        return FetchFmsRecordsOutput(
            ok=True,
            client_job_code=data.client_job_code,
            records=[sample_record()],
            errors=[],
            latency_ms=1,
        )

    async def fake_generate(_prompt):
        return LlmResult(
            ok=True,
            provider="openai",
            model="gpt-4o-mini",
            content="Status pending hai.",
            latency_ms=2,
        )

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="Mera current status kya hai?"),
        client_user(),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )

    assert "source citation missing" in response.reply


def test_admin_client_list_is_deterministic_and_cited():
    records = [
        sample_record("HOACPL-F25F-TL01", "FMS1", 7),
        sample_record("HOACPL-F25F-TL01", "FMS2", 12),
        sample_record("ITPL-F25E-SUBCC02", "FMS4", 20),
    ]

    reply = format_admin_client_list(records)

    # Summary caption.
    assert "Unique Client Job Codes: 2" in reply
    # Valid GFM table: header + separator row.
    assert "| # | Client Job Code | Client Name | Rows | Source |" in reply
    assert "|---|---|---|---:|---|" in reply
    # A data row with grouped count and per-row source citation.
    assert "| 1 | HOACPL-F25F-TL01 |" in reply  # sorted first, 2 grouped rows
    assert "FMS1 row 7, col Client Job Code" in reply
    # Every data row is valid table markup (starts and ends with a pipe).
    data_rows = [ln for ln in reply.splitlines() if ln.startswith("| ") and "row" in ln]
    assert len(data_rows) == 2  # two unique codes


def test_admin_client_list_escapes_pipes_in_names():
    record = sample_record("ABC-F25F-TL01", "FMS1", 7)
    record.client_name = "Foo | Bar Ltd"

    reply = format_admin_client_list([record])

    # A literal pipe in the name must be escaped so it does not split the cell.
    assert r"Foo \| Bar Ltd" in reply


@pytest.mark.asyncio
async def test_admin_list_query_does_not_call_llm_when_records_supplied():
    async def fake_generate(_prompt):
        raise AssertionError("llm should not be called for list query")

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="list all client job codes"),
        admin_user(),
        generate_answer_fn=fake_generate,
        admin_records=[sample_record(), sample_record("ITPL-F25E-SUBCC02", "FMS4", 20)],
    )

    assert "Unique Client Job Codes: 2" in response.reply
    assert "HOACPL-F25F-TL01" in response.reply


@pytest.mark.asyncio
async def test_admin_tell_me_some_client_job_codes_is_list_query():
    async def fake_generate(_prompt):
        raise AssertionError("llm should not be called for job-code list query")

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="Tell me some client job codes"),
        admin_user(),
        generate_answer_fn=fake_generate,
        admin_records=[sample_record(), sample_record("ITPL-F25E-SUBCC02", "FMS4", 20)],
    )

    assert "Unique Client Job Codes: 2" in response.reply
    assert "ITPL-F25E-SUBCC02" in response.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        "Give me all client job codes",
        "give me all the client codes",
        "show me all clients",
        "how many client codes are there",
        "total clients",
        "sabhi client codes batao",
        "list clients",
    ],
)
async def test_admin_list_intent_routes_to_deterministic_table(message):
    """Natural 'list all codes' phrasings must hit the deterministic table
    path (all records, real table) and never the record-capped LLM path."""

    async def fake_generate(_prompt):
        raise AssertionError(f"llm should not be called for list query: {message!r}")

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message=message),
        admin_user(),
        generate_answer_fn=fake_generate,
        admin_records=[sample_record(), sample_record("ITPL-F25E-SUBCC02", "FMS4", 20)],
    )

    # Full deterministic table with both codes, not an LLM subset.
    assert "Unique Client Job Codes: 2" in response.reply
    assert "|---|---|---|---:|---|" in response.reply
    assert "HOACPL-F25F-TL01" in response.reply
    assert "ITPL-F25E-SUBCC02" in response.reply


def test_prompt_payload_truncates_large_values_and_caps_fields():
    record = sample_record()
    record.step_fields = {
        **{f"Step 1 - Remark {idx}": "x" * 1000 for idx in range(50)},
        **record.step_fields,
    }

    payload = records_to_prompt_payload("status batao", [record], max_records=1, max_fields_per_record=10)

    fields = payload[0]["fields"]
    assert len(fields) <= 10
    assert any("[truncated]" in value for value in fields.values())


def test_prompt_builder_stays_within_message_char_limit_for_wide_records():
    """Broad admin queries over many wide FMS rows must not overflow the LLM
    message limit (regression for the string_too_long 500)."""
    from app.fms_v2.chat import PROMPT_CONTENT_CHAR_BUDGET

    def wide_record(index):
        record = sample_record(code=f"WIDE-F25F-{index:03d}", row=index + 7)
        record.step_fields = {
            f"Step {step} - Remark": "y" * 400 for step in range(200)
        }
        record.source_columns = {
            name: source("FMS1", index + 7, name, "Z")
            for name in record.step_fields
        }
        return record

    records = [wide_record(i) for i in range(40)]

    prompt = build_fms_chat_prompt(
        role="admin",
        question="give me an overview of all files",
        records=records,
        authenticated_client_job_code=None,
    )

    # Building the prompt must not raise, and the serialized user message must
    # fit the char budget (and thus the LlmMessage 80k limit).
    user_message = next(m for m in prompt.messages if m.role == "user")
    assert len(user_message.content) <= PROMPT_CONTENT_CHAR_BUDGET


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "Hello!",
        "hey",
        "namaste",
        "namaste kaise ho",
        "hello there",
        "good morning sir",
        "how are you",
        "help",
        "thanks",
        "ok",
        "  ",
        "yo",
    ],
)
def test_greeting_detector_matches_small_talk(message):
    from app.fms_v2.chat import is_greeting_or_small_talk

    assert is_greeting_or_small_talk(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "HOACPL-F25F-TL01 ka status batao",
        "show me all client codes",
        "what is the planned date for step 3",
        "hi, HOACPL-F25F-TL01 ka status kya hai",
        "hello, mujhe loan amount batao",
        "namaste, status kya hai",
    ],
)
def test_greeting_detector_ignores_data_queries(message):
    from app.fms_v2.chat import is_greeting_or_small_talk

    assert is_greeting_or_small_talk(message) is False


@pytest.mark.asyncio
async def test_greeting_calls_llm_without_fetch_and_skips_citation_guard():
    """Greetings skip the sheet fetch but are answered by the LLM, and the
    citation guard must NOT reject the (citation-free) small-talk reply."""
    calls = {}

    async def fake_fetch(_data):
        raise AssertionError("fetch should not be called for a greeting")

    async def fake_generate(prompt):
        calls["prompt"] = prompt
        # A natural greeting reply with no source citation.
        return LlmResult(ok=True, provider="groq", model="x", content="Hello! How can I help?")

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="hi"),
        admin_user(),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )

    assert response.reply == "Hello! How can I help?"
    assert "prompt" in calls  # LLM was called


@pytest.mark.asyncio
async def test_greeting_falls_back_when_llm_fails():
    from app.fms_v2.chat import SMALL_TALK_FALLBACK_REPLY

    async def fake_fetch(_data):
        raise AssertionError("fetch should not be called for a greeting")

    async def fake_generate(_prompt):
        return LlmResult(ok=False, error="all providers failed")

    response = await chat_with_fms_v2(
        FmsV2ChatMessage(message="hi"),
        admin_user(),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )

    assert response.reply == SMALL_TALK_FALLBACK_REPLY
