"""Dry-run tests for the FMS v2 sheet parser/fetch tool."""

import pytest

from app.fms_v2.models import FetchFmsRecordsInput
from app.fms_v2.sheets import (
    detect_header_row_index,
    fetch_fms_records_by_client_code,
    parse_fms_sheet_values,
)


def fms1_values():
    return [
        ["FMS1"],
        [],
        [],
        [""] * 20,
        [""] * 10 + ["Step 1", "", "", "", "", "Step 2", "", "", "", ""],
        [
            "Timestamp",
            "Client Name",
            "Project Name",
            "Proposal Type",
            "Concerned Person",
            "Team Leader",
            "Team Engaged",
            "Total Loan Amount",
            "Sublimit of CC (LC/BG/WCDL) Amt (Cr)",
            "Client Job Code",
            "Group",
            "Doer",
            "Planned",
            "Actual",
            "URL",
            "Remark",
            "Doer",
            "Planned",
            "Actual",
            "Remark",
        ],
        [
            "2026-01-01",
            "Hindustan Oil",
            "Expansion",
            "Fresh",
            "Rahul",
            "Lead 1",
            "Team A",
            "10",
            "2",
            " hoacpl-f25f-tl01 ",
            "Docs",
            "Asha",
            "2026-01-10",
            "2026-01-11",
            "https://example.com/doc",
            "Done",
            "Neha",
            "2026-01-12",
            "",
            "Pending",
        ],
        [
            "2026-01-02",
            "Other Client",
            "Plant",
            "Fresh",
            "Ravi",
            "Lead 2",
            "Team B",
            "5",
            "",
            "OTHER-F25F-TL01",
            "Docs",
            "Maya",
        ],
    ]


def fms2_values():
    return [
        ["FMS2"],
        [],
        [],
        [""] * 12,
        [""] * 10 + ["Step 9", "", ""],
        [
            "Date of Submit",
            "Client Job Code",
            "Client Name",
            "Name of Project",
            "Total Loan Amount",
            "Bank Name & Branch Name",
            "Bank Relationship Manager",
            "Team Engaged",
            "Receiving Copy",
            "Soft & Hard Copy",
            "Doer",
            "Planned",
            "Actual",
        ],
        [
            "2026-02-01",
            "HOACPL-F25F-TL01",
            "Hindustan Oil",
            "Expansion",
            "10",
            "Bank A",
            "RM 1",
            "Team A",
            "Yes",
            "Yes",
            "Kiran",
            "2026-02-05",
            "2026-02-06",
        ],
        [
            "2026-02-03",
            "HOACPL-F25F-TL01",
            "Hindustan Oil",
            "Expansion",
            "10",
            "Bank B",
            "RM 2",
            "Team A",
            "Yes",
            "No",
            "Kiran",
            "2026-02-08",
            "",
        ],
    ]


@pytest.mark.asyncio
async def test_fetch_preserves_duplicate_workflow_columns_and_sources():
    result = await fetch_fms_records_by_client_code(
        FetchFmsRecordsInput(client_job_code="HOACPL-F25F-TL01", sheets=["FMS1"]),
        values_by_sheet={"FMS1": fms1_values()},
    )

    assert result.ok is True
    assert result.client_job_code == "HOACPL-F25F-TL01"
    assert len(result.records) == 1

    record = result.records[0]
    assert record.sheet_name == "FMS1"
    assert record.row_number == 7
    assert record.client_name == "Hindustan Oil"
    assert record.base_fields["Client Job Code"] == "hoacpl-f25f-tl01"
    assert record.step_fields["Step 1 - Doer"] == "Asha"
    assert record.step_fields["Step 2 - Doer"] == "Neha"
    assert record.source_columns["Client Job Code"].column_letter == "J"
    assert record.source_columns["Step 1 - URL"].row_number == 7
    assert record.source_columns["Step 1 - URL"].column_letter == "O"


def test_header_row_detection_prefers_fms_header_row():
    assert detect_header_row_index(fms1_values()) == 5
    assert detect_header_row_index(fms2_values()) == 5


def test_parser_missing_code_returns_no_records_after_filtering():
    records = parse_fms_sheet_values("FMS1", fms1_values())
    assert {record.client_job_code for record in records} == {
        "HOACPL-F25F-TL01",
        "OTHER-F25F-TL01",
    }


@pytest.mark.asyncio
async def test_fetch_missing_code_returns_ok_empty_records():
    result = await fetch_fms_records_by_client_code(
        FetchFmsRecordsInput(client_job_code="MISSING-F25F-TL01", sheets=["FMS1"]),
        values_by_sheet={"FMS1": fms1_values()},
    )

    assert result.ok is True
    assert result.records == []
    assert result.errors == []


@pytest.mark.asyncio
async def test_sheet_specific_client_code_columns_and_multiple_rows():
    result = await fetch_fms_records_by_client_code(
        FetchFmsRecordsInput(
            client_job_code="HOACPL-F25F-TL01",
            sheets=["FMS1", "FMS2", "FMS3", "FMS4"],
        ),
        values_by_sheet={
            "FMS1": fms1_values(),
            "FMS2": fms2_values(),
            "FMS3": fms1_values(),
            "FMS4": fms2_values(),
        },
    )

    assert result.ok is True
    assert len(result.records) == 6
    assert [record.sheet_name for record in result.records].count("FMS2") == 2
    assert [record.sheet_name for record in result.records].count("FMS4") == 2
    assert all(record.client_job_code == "HOACPL-F25F-TL01" for record in result.records)
    assert result.records[1].source_columns["Client Job Code"].column_letter == "B"


@pytest.mark.asyncio
async def test_fetcher_retries_before_success():
    attempts = {"count": 0}

    async def flaky_fetcher(sheet_name):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError(f"temporary failure for {sheet_name}")
        return fms1_values()

    result = await fetch_fms_records_by_client_code(
        FetchFmsRecordsInput(client_job_code="HOACPL-F25F-TL01", sheets=["FMS1"]),
        fetcher=flaky_fetcher,
        timeout_seconds=1,
        attempts=2,
    )

    assert result.ok is True
    assert attempts["count"] == 2
    assert len(result.records) == 1
