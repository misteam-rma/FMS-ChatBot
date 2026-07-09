"""Deterministic Google Sheets access for FMS v2."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.config import settings as app_settings
from app.fms_v2.config import (
    FMS_CLIENT_CODE_COLUMNS,
    FMS_HEADER_ROW,
    FMS_SHEET_NAMES,
    FmsSheetName,
    get_fms_v2_settings,
)
from app.fms_v2.models import (
    FetchFmsRecordsInput,
    FetchFmsRecordsOutput,
    FmsRecord,
    SourceColumn,
    ToolError,
)


logger = logging.getLogger("botivate_api.fms_v2.sheets")

SheetValues = list[list[str]]
SheetValueFetcher = Callable[[FmsSheetName], SheetValues | Awaitable[SheetValues]]

HEADER_SCORE_TERMS = {
    "client job code": 40,
    "client name": 28,
    "project name": 18,
    "name of project": 18,
    "status": 14,
    "doer": 12,
    "planned": 10,
    "actual": 10,
    "url": 8,
    "remark": 8,
}

REPEATED_WORKFLOW_HEADERS = {
    "group",
    "doer",
    "planned",
    "actual",
    "url",
    "remark",
    "status",
}

BASE_FIELD_NAMES = {
    "timestamp",
    "date of submit",
    "client name",
    "project name",
    "proposal type",
    "concerned person",
    "team leader",
    "team engaged",
    "total loan amount",
    "sublimit of cc (lc/bg/wcdl) amt (cr)",
    "client job code",
    "name of project",
    "bank name & branch name",
    "bank relationship manager",
    "receiving copy",
    "soft & hard copy",
}


async def fetch_fms_records_by_client_code(
    data: FetchFmsRecordsInput | dict[str, Any],
    *,
    values_by_sheet: Mapping[FmsSheetName, SheetValues] | None = None,
    fetcher: SheetValueFetcher | None = None,
    timeout_seconds: float = 10,
    attempts: int = 3,
) -> FetchFmsRecordsOutput:
    """Fetch matching FMS records by Client Job Code from FMS1-FMS4 only."""

    started = time.perf_counter()
    validated = FetchFmsRecordsInput.model_validate(data)
    target_code = normalize_client_job_code(validated.client_job_code)
    errors: list[ToolError] = []
    records: list[FmsRecord] = []

    logger.info(
        "FMS v2 fetch start client_job_code=%s sheets=%s",
        target_code,
        ",".join(validated.sheets),
    )

    for sheet_name in validated.sheets:
        if sheet_name not in FMS_SHEET_NAMES:
            errors.append(
                ToolError(
                    sheet_name=None,
                    code="invalid_sheet",
                    message=f"Sheet '{sheet_name}' is not allowed for FMS v2.",
                )
            )
            continue

        try:
            if values_by_sheet is not None:
                values = values_by_sheet.get(sheet_name, [])
            else:
                values = await fetch_sheet_values_with_retry(
                    sheet_name,
                    fetcher=fetcher,
                    timeout_seconds=timeout_seconds,
                    attempts=attempts,
                )

            parsed = parse_fms_sheet_values(sheet_name, values)
            records.extend(
                record
                for record in parsed
                if normalize_client_job_code(record.client_job_code) == target_code
            )
        except Exception as exc:
            logger.exception("FMS v2 fetch failed for sheet=%s", sheet_name)
            errors.append(
                ToolError(
                    sheet_name=sheet_name,
                    code="sheet_fetch_failed",
                    message=str(exc),
                )
            )

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "FMS v2 fetch done client_job_code=%s records=%s errors=%s latency_ms=%s",
        target_code,
        len(records),
        len(errors),
        latency_ms,
    )

    return FetchFmsRecordsOutput(
        ok=not errors,
        client_job_code=target_code,
        records=records,
        errors=errors,
        latency_ms=latency_ms,
    )


async def fetch_sheet_values_with_retry(
    sheet_name: FmsSheetName,
    *,
    fetcher: SheetValueFetcher | None = None,
    timeout_seconds: float = 10,
    attempts: int = 3,
) -> SheetValues:
    """Fetch one worksheet's raw values with timeout and exponential backoff."""

    last_error: Exception | None = None
    bounded_attempts = max(1, attempts)

    for attempt in range(1, bounded_attempts + 1):
        try:
            return await asyncio.wait_for(
                _call_sheet_value_fetcher(sheet_name, fetcher=fetcher),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "FMS v2 sheet fetch attempt failed sheet=%s attempt=%s/%s error=%s",
                sheet_name,
                attempt,
                bounded_attempts,
                exc,
            )
            if attempt < bounded_attempts:
                await asyncio.sleep(0.2 * (2 ** (attempt - 1)))

    assert last_error is not None
    raise last_error


async def _call_sheet_value_fetcher(
    sheet_name: FmsSheetName,
    *,
    fetcher: SheetValueFetcher | None,
) -> SheetValues:
    if fetcher is not None:
        result = fetcher(sheet_name)
        if inspect.isawaitable(result):
            return await result
        return result

    return await asyncio.to_thread(fetch_sheet_values_from_google, sheet_name)


def fetch_sheet_values_from_google(sheet_name: FmsSheetName) -> SheetValues:
    """Read raw values for one allowed FMS worksheet using service account auth."""

    if sheet_name not in FMS_SHEET_NAMES:
        raise ValueError(f"Sheet '{sheet_name}' is not allowed for FMS v2.")

    return fetch_worksheet_values(sheet_name)


def fetch_worksheet_values(worksheet_name: str) -> SheetValues:
    """Read raw values for any worksheet in the configured workbook.

    Used by both the FMS1-FMS4 parser and the dashboard (NEW DASH / Completed
    Dash / RUF Help Sheet / Sanction Letter) readers. Read-only.
    """

    import gspread

    fms_settings = get_fms_v2_settings()
    service_account_json = app_settings.google_service_account_json
    if not service_account_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is required for FMS v2 sheet access.")

    try:
        service_account_info = json.loads(service_account_json)
    except json.JSONDecodeError as exc:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc

    client = gspread.service_account_from_dict(service_account_info)
    spreadsheet = client.open_by_key(fms_settings.workbook_id)
    worksheet = spreadsheet.worksheet(worksheet_name)
    return worksheet.get_all_values()


def parse_fms_sheet_values(sheet_name: FmsSheetName, values: SheetValues) -> list[FmsRecord]:
    """Parse raw FMS worksheet values into structured records with citations."""

    if sheet_name not in FMS_SHEET_NAMES:
        raise ValueError(f"Sheet '{sheet_name}' is not allowed for FMS v2.")
    if not values:
        return []

    header_index = detect_header_row_index(values)
    columns = build_unique_column_names(values, header_index)
    records: list[FmsRecord] = []

    client_code_index = _client_code_index(sheet_name, columns)
    client_name_index = _first_matching_column(columns, {"client name"})

    for offset, row in enumerate(values[header_index + 1 :], start=header_index + 2):
        if not any(str(cell).strip() for cell in row):
            continue

        client_code = normalize_client_job_code(cell_value(row, client_code_index))
        if not client_code:
            continue

        base_fields: dict[str, Any] = {}
        step_fields: dict[str, Any] = {}
        source_columns: dict[str, SourceColumn] = {}

        for col_index, column in enumerate(columns):
            value = cell_value(row, col_index)
            if not value:
                continue

            target = base_fields if is_base_column(column) else step_fields
            target[column.unique_name] = value
            source_columns[column.unique_name] = SourceColumn(
                sheet_name=sheet_name,
                row_number=offset,
                column_name=column.unique_name,
                column_letter=column_letter(col_index),
            )

        records.append(
            FmsRecord(
                sheet_name=sheet_name,
                row_number=offset,
                client_job_code=client_code,
                client_name=cell_value(row, client_name_index) if client_name_index is not None else "",
                base_fields=base_fields,
                step_fields=step_fields,
                source_columns=source_columns,
            )
        )

    return records


class ParsedColumn:
    """Internal parsed column metadata."""

    def __init__(self, unique_name: str, raw_header: str, index: int):
        self.unique_name = unique_name
        self.raw_header = raw_header
        self.index = index


def detect_header_row_index(values: SheetValues) -> int:
    """Detect the most likely header row, preferring the confirmed row 6."""

    best_index = max(0, min(FMS_HEADER_ROW - 1, len(values) - 1))
    best_score = -1

    for index, row in enumerate(values[:20]):
        normalized = [normalize_header(cell) for cell in row]
        score = min(sum(1 for cell in normalized if cell), 12)
        for cell in normalized:
            score += HEADER_SCORE_TERMS.get(cell, 0)

        if score > best_score:
            best_score = score
            best_index = index

    return best_index


def build_unique_column_names(values: SheetValues, header_index: int) -> list[ParsedColumn]:
    """Build unique names from header row plus parent/context rows."""

    max_width = max((len(row) for row in values), default=0)
    header_row = values[header_index] if header_index < len(values) else []
    # Context closest to the header carries the step/task group labels. Earlier
    # title/banner rows are intentionally ignored to avoid noisy column names.
    context_start = max(0, header_index - 2)
    context_rows = [fill_forward(row, max_width) for row in values[context_start:header_index]]
    seen: dict[str, int] = {}
    columns: list[ParsedColumn] = []

    for col_index in range(max_width):
        raw_header = cell_value(header_row, col_index)
        base_header = raw_header or f"Column {column_letter(col_index)}"
        context_parts = []
        for row in context_rows:
            context_value = cell_value(row, col_index)
            if context_value and normalize_header(context_value) not in {
                normalize_header(part) for part in context_parts
            }:
                context_parts.append(context_value)

        should_prefix = (
            normalize_header(base_header) in REPEATED_WORKFLOW_HEADERS
            or normalize_header(base_header) in seen
            or not raw_header
        )
        candidate = (
            " - ".join(context_parts + [base_header])
            if should_prefix and context_parts
            else base_header
        )

        count = seen.get(normalize_header(candidate), 0)
        seen[normalize_header(candidate)] = count + 1
        unique_name = candidate if count == 0 else f"{candidate} ({count + 1})"
        columns.append(ParsedColumn(unique_name=unique_name, raw_header=base_header, index=col_index))

    return columns


# Unicode dash variants that must fold to an ASCII "-" so codes like
# "GA-F25F-TL11" match regardless of whether a non-breaking hyphen (U+2011),
# en/em dash, or figure dash slipped in via a sheet, keyboard, or autofill.
_DASH_VARIANTS = str.maketrans(
    {c: "-" for c in "‐‑‒–—―−﹘﹣－"}
)


def normalize_client_job_code(value: str) -> str:
    """Normalize Client Job Code for exact matching.

    Folds Unicode dash variants to ASCII '-', uppercases, trims, and collapses
    internal whitespace.
    """

    text = str(value or "").translate(_DASH_VARIANTS)
    return " ".join(text.strip().upper().split())


def normalize_header(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def cell_value(row: list[Any], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def fill_forward(row: list[Any], width: int) -> list[str]:
    filled: list[str] = []
    last = ""
    for index in range(width):
        value = cell_value(row, index)
        if value:
            last = value
        filled.append(last)
    return filled


def column_letter(index: int) -> str:
    """Return a spreadsheet column letter for a zero-based column index."""

    if index < 0:
        raise ValueError("Column index must be non-negative.")

    number = index + 1
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def column_letter_to_index(letter: str) -> int:
    number = 0
    for char in letter.upper():
        if not char.isalpha():
            continue
        number = number * 26 + (ord(char) - ord("A") + 1)
    return number - 1


def is_base_column(column: ParsedColumn) -> bool:
    return normalize_header(column.raw_header) in BASE_FIELD_NAMES


def _client_code_index(sheet_name: FmsSheetName, columns: list[ParsedColumn]) -> int:
    expected_index = column_letter_to_index(FMS_CLIENT_CODE_COLUMNS[sheet_name])
    if expected_index < len(columns) and normalize_header(columns[expected_index].raw_header) == "client job code":
        return expected_index

    found = _first_matching_column(columns, {"client job code"})
    if found is not None:
        return found

    return expected_index


def _first_matching_column(columns: list[ParsedColumn], names: set[str]) -> int | None:
    for column in columns:
        if normalize_header(column.raw_header) in names:
            return column.index
    return None
