"""The LLM path must be a superset of the deterministic buttons: a
natural-language document query returns the same real download links the FMS
button gives, by pulling RUF Help Sheet / Sanction Letter records into the LLM
context."""

import pytest

from app.fms_v2 import chat as chat_mod
from app.fms_v2.admin_sources import is_document_query
from app.fms_v2.llm import LlmResult
from app.fms_v2.models import (
    FetchFmsRecordsOutput,
    FmsRecord,
    FmsV2ChatMessage,
    TokenPayload,
)


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("I need my sanction letter document", True),
        ("give me the search report link", True),
        ("show me the TEV report", True),
        ("download my valuation", True),
        ("what is my current status", False),
        ("when is the planned date", False),
    ],
)
def test_document_query_detector(msg, expected):
    assert is_document_query(msg) is expected


@pytest.mark.asyncio
async def test_client_document_query_includes_link_records(monkeypatch):
    """A client doc query must reach the LLM with the real link records so it
    can answer with a download link (not a useless 'Sanction Letter' echo)."""

    captured = {}

    async def fake_fetch(data):
        return FetchFmsRecordsOutput(
            ok=True, client_job_code=data.client_job_code,
            records=[FmsRecord(sheet_name="FMS1", row_number=7,
                               client_job_code=data.client_job_code, client_name="X")],
            errors=[], latency_ms=1,
        )

    async def fake_doc_links(code):
        return [FmsRecord(
            sheet_name="RUF Help Sheet", row_number=1, client_job_code=code,
            base_fields={"Sanction Letter": "https://drive.google.com/uc?id=ABC&export=download"},
            source_columns={},
        )]

    async def fake_generate(prompt):
        # Record what records reached the prompt.
        captured["prompt"] = prompt
        return LlmResult(ok=True, provider="groq", model="x",
                         content="Here: [Sanction Letter](https://drive.google.com/uc?id=ABC&export=download) "
                                 "Source: RUF Help Sheet row 1, column Sanction Letter")

    monkeypatch.setattr(chat_mod, "document_link_records", fake_doc_links)

    resp = await chat_mod.chat_with_fms_v2(
        FmsV2ChatMessage(message="I need my sanction letter document"),
        TokenPayload(employee_id="HOACPL-F25F-TL01", user_type="client",
                     client_job_code="HOACPL-F25F-TL01"),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )

    # The link record's URL must have been serialized into the prompt.
    serialized = str(captured["prompt"].messages)
    assert "drive.google.com/uc?id=ABC" in serialized
    assert "drive.google.com/uc?id=ABC" in resp.reply


@pytest.mark.asyncio
async def test_client_non_doc_query_skips_link_fetch(monkeypatch):
    """A non-document query must NOT pull document links (stays lean/fast)."""

    called = {"docs": False}

    async def fake_fetch(data):
        return FetchFmsRecordsOutput(
            ok=True, client_job_code=data.client_job_code,
            records=[FmsRecord(sheet_name="FMS1", row_number=7,
                               client_job_code=data.client_job_code, client_name="X")],
            errors=[], latency_ms=1,
        )

    async def fake_doc_links(code):
        called["docs"] = True
        return []

    async def fake_generate(_prompt):
        return LlmResult(ok=True, provider="groq", model="x",
                         content="Status Done. Source: FMS1 row 7, column Status.")

    monkeypatch.setattr(chat_mod, "document_link_records", fake_doc_links)

    await chat_mod.chat_with_fms_v2(
        FmsV2ChatMessage(message="what is my current status"),
        TokenPayload(employee_id="HOACPL-F25F-TL01", user_type="client",
                     client_job_code="HOACPL-F25F-TL01"),
        fetch_records_fn=fake_fetch,
        generate_answer_fn=fake_generate,
    )
    assert called["docs"] is False
