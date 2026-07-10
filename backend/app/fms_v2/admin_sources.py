"""Extra data sources for ADMIN queries beyond FMS1-FMS4.

Admins can reach core tabs the client path never touches: RAW DATA (client
master incl. phone numbers), NEW DASH and Completed Dash (project dashboards).
Rows are converted into FmsRecord objects so they flow through the existing
ranking + prompt + citation pipeline unchanged.

Which extra tabs are pulled is decided per-query by keyword (e.g. a phone/contact
question pulls RAW DATA) so prompts stay within the token budget.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.fms_v2.dash import find_document_links
from app.fms_v2.models import FmsRecord, SourceColumn
from app.fms_v2.sheets import (
    column_letter,
    fetch_worksheet_values,
    normalize_client_job_code,
)


logger = logging.getLogger("botivate_api.fms_v2.admin_sources")

RAW_DATA = "RAW DATA"
NEW_DASH = "NEW DASH"
COMPLETED_DASH = "Completed Dash"

_JOB_CODE_RE = re.compile(r"-F\d{2}[A-Z]-", re.IGNORECASE)

# Document/link intent — pulls RUF Help Sheet + Sanction Letter download links.
_DOC_TERMS = re.compile(
    r"\b(document|documents|doc|docs|letter|sanction|search\s*report|"
    r"valuation|tev|ddr|report|download|link|links|copy|file|attachment)\b",
    re.IGNORECASE,
)

# Keyword triggers that widen an admin query to extra tabs.
_PHONE_TERMS = re.compile(
    r"\b(phone|mobile|number|numbers|contact|contacts|whatsapp|call|reach)\b",
    re.IGNORECASE,
)
_DASH_TERMS = re.compile(
    r"\b(dashboard|progress|completion|percent|ongoing|completed|"
    r"total projects|how many projects|project overview)\b",
    re.IGNORECASE,
)
# Follow-up phrases that carry no topic of their own — reuse the prior context.
_FOLLOWUP_TERMS = re.compile(
    r"\b(list more|show more|more|next|others?|another|continue|and more|"
    r"give me all|all of them|rest)\b",
    re.IGNORECASE,
)


def _cell(row: list, idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) and row[idx] is not None else ""


def _header_row_index(values: list[list[str]]) -> int:
    """Find the row that carries column labels (contains 'Client Job Code' or a
    known dashboard header)."""

    for i, row in enumerate(values[:12]):
        joined = " ".join(str(c).lower() for c in row)
        if "client job code" in joined or "client code" in joined or "s. no." in joined:
            return i
    return 0


def _rows_to_records(
    sheet_name: str,
    values: list[list[str]],
    code_col: int,
    keep_cols: set[str] | None = None,
) -> list[FmsRecord]:
    """Turn a flat tab into FmsRecords keyed by its Client Job Code column.

    `keep_cols` (lowercased header names) restricts which columns are kept, to
    keep records lean for the prompt. None keeps all non-empty columns.
    """

    if not values:
        return []
    header_idx = _header_row_index(values)
    headers = values[header_idx]
    records: list[FmsRecord] = []
    for offset, row in enumerate(values[header_idx + 1 :], start=header_idx + 2):
        code = _cell(row, code_col)
        if not code or not _JOB_CODE_RE.search(code):
            continue
        fields: dict = {}
        sources: dict = {}
        for c, raw in enumerate(row):
            val = str(raw).strip() if raw is not None else ""
            if not val:
                continue
            name = _cell(headers, c) or f"Column {column_letter(c)}"
            if keep_cols is not None and name.lower() not in keep_cols:
                continue
            # Avoid duplicate header names collapsing data.
            if name in fields:
                name = f"{name} ({column_letter(c)})"
            fields[name] = val
            sources[name] = SourceColumn(
                sheet_name=sheet_name,
                row_number=offset,
                column_name=name,
                column_letter=column_letter(c),
            )
        client_name = fields.get("Client Name") or fields.get("CLIENT NAME") or ""
        records.append(
            FmsRecord(
                sheet_name=sheet_name,
                row_number=offset,
                client_job_code=normalize_client_job_code(code),
                client_name=client_name,
                base_fields=fields,
                step_fields={},
                source_columns=sources,
            )
        )
    return records


async def fetch_extra_admin_records(
    question: str, recent_context: str = ""
) -> list[FmsRecord]:
    """Return extra records from RAW DATA / dashboards relevant to the query.

    Pulls a tab when the question's keywords call for it. A context-free
    follow-up ("list more", "give me all") reuses `recent_context` (the prior
    turns) so the topic carries over. Returns [] when no extra source is needed.
    """

    text = question
    # A pure follow-up borrows intent from the recent conversation.
    if _FOLLOWUP_TERMS.search(question) and recent_context:
        text = f"{question}\n{recent_context}"

    # Lean column set for RAW DATA so 96 rows fit the prompt budget.
    raw_keep = {
        "client job code", "client name", "project name", "mobile number",
        "total loan amount", "team leader", "proposal type",
    }

    tabs: list[tuple[str, int, set[str] | None]] = []
    if _PHONE_TERMS.search(text):
        tabs.append((RAW_DATA, 15, raw_keep))  # RAW DATA: Client Job Code col P(15)
    if _DASH_TERMS.search(text):
        tabs.append((NEW_DASH, 1, None))
        tabs.append((COMPLETED_DASH, 1, None))

    if not tabs:
        return []

    async def _load(name: str, col: int, keep: set[str] | None) -> list[FmsRecord]:
        try:
            values = await asyncio.to_thread(fetch_worksheet_values, name)
            return _rows_to_records(name, values, col, keep)
        except Exception:
            logger.exception("FMS v2 admin extra-source fetch failed tab=%s", name)
            return []

    results = await asyncio.gather(*[_load(name, col, keep) for name, col, keep in tabs])
    records = [r for group in results for r in group]
    logger.info(
        "FMS v2 admin extra sources tabs=%s records=%s",
        ",".join(t[0] for t in tabs),
        len(records),
    )
    return records


def is_document_query(message: str) -> bool:
    """True if the message asks for a document / report / sanction-letter link."""

    return bool(_DOC_TERMS.search(str(message or "")))


async def document_link_records(client_code: str) -> list[FmsRecord]:
    """Fetch a code's document links (RUF Help Sheet + Sanction Letter) and
    return them as FmsRecords so the LLM can answer document queries with real
    download links — the natural-language equivalent of the FMS button."""

    code = normalize_client_job_code(client_code)
    if not code:
        return []
    links = await find_document_links(code)
    if not links:
        return []

    fields: dict = {}
    sources: dict = {}
    # RUF Help Sheet holds Search/Valuation/TEV/DDR; Sanction Letter holds its own.
    for label, url in links.items():
        fields[label] = url
        sheet = "Sanction Letter" if label == "Sanction Letter" else "RUF Help Sheet"
        sources[label] = SourceColumn(
            sheet_name=sheet, row_number=1, column_name=label,
        )
    return [
        FmsRecord(
            sheet_name="RUF Help Sheet",
            row_number=1,
            client_job_code=code,
            client_name="",
            base_fields=fields,
            step_fields={},
            source_columns=sources,
        )
    ]
