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
    assert "Default to Hinglish in Latin script" in prompt.messages[0].content
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

    assert "Unique Client Job Codes: 2" in reply
    assert "HOACPL-F25F-TL01" in reply
    assert "Rows: 2" in reply
    assert "Source: FMS1 row 7, column Client Job Code." in reply


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
