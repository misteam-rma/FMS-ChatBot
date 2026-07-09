"""Deterministic dashboard readers for the hardcoded menu intents.

Ports the data model of the RMA Apps Script bot (.agents/file.gs) onto the
NEW-FMS-RMA workbook. The per-project rows live in the `NEW DASH` (ongoing) and
`Completed Dash` (completed) tabs; document links live in `RUF Help Sheet` and
`Sanction Letter`.

Column indices below are verified against the live workbook, not copied blindly
from file.gs (Sanction Letter differs: real layout is [0]=code, [1]=bank,
[2]=link). All access is read-only.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.fms_v2.sheets import fetch_worksheet_values, normalize_client_job_code


logger = logging.getLogger("botivate_api.fms_v2.dash")

NEW_DASH = "NEW DASH"
COMPLETED_DASH = "Completed Dash"
RUF_HELP_SHEET = "RUF Help Sheet"
SANCTION_SHEET = "Sanction Letter"

# Per-project dashboard columns (0-based), verified against the live sheet.
COL_SNO = 0
COL_JOB_CODE = 1
COL_CLIENT_NAME = 2
COL_BANK_NAME = 3
COL_PROJECT_NAME = 4
COL_AMOUNT = 5
COL_TL_NAME = 8
COL_COMPLETION_PCT = 28

# Workflow step columns and their human names, per file.gs.
NEW_DASH_STEP_COLS = [9, 10, 15, 16, 17, 18, 19, 20, 21, 22, 23]
NEW_DASH_STEP_NAMES = [
    "Step 1: Collection of Primary Document",
    "Step 2: Collection of Secondary Document",
    "Step 3: Preparation of Your Set",
    "Step 4: Project Report Prepared",
    "Step 5: Preparation of Board Note",
    "Step 6: Search & Valuation of Primary Property",
    "Step 7: Search & Valuation of Collateral Property",
    "Step 8: TEV Report Prepared",
    "Step 9: DDR Report Prepared",
    "Step 10: Query of Bank Resolved",
    "Step 11: Received Sanction Letter From Bank",
]
COMP_DASH_STEP_COLS = [9, 10, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26]
COMP_DASH_STEP_NAMES = NEW_DASH_STEP_NAMES + [
    "Step 12: Completion of DOC as per Sanction Letter",
    "Step 13: Completion of PDC as per Sanction Letter",
    "Step 14: Term Loan Ready for Disbursement",
]

# Documents required per step (from file.gs STEP_DOCUMENTS; verify with RMA).
STEP_DOCUMENTS: dict[str, list[str]] = {
    "Step 1": ["PAN Card", "Aadhaar Card", "GST Certificate", "MOA/AOA", "Board Resolution"],
    "Step 2": ["ITR (3 Years)", "Financial Statements", "Bank Statements (12 Months)", "GST Returns"],
    "Step 3": ["Loan Application Form", "Project Report Draft", "CMA Data"],
    "Step 4": ["Detailed Project Report", "CMA Analysis", "Fund Flow Statement"],
    "Step 5": ["Board Note Draft", "Credit Appraisal Note"],
    "Step 6": ["Property Documents", "Search Report", "Valuation Report (Primary)"],
    "Step 7": ["Collateral Property Docs", "Search Report (Collateral)", "Valuation Report (Collateral)"],
    "Step 8": ["TEV Report Application", "Technical Feasibility Report"],
    "Step 9": ["DDR Application", "Due Diligence Documents"],
    "Step 10": ["Bank Query Response", "Additional Clarifications"],
    "Step 11": ["Sanction Letter", "Terms & Conditions"],
    "Step 12": ["DOC Checklist as per Sanction", "Signed Documents", "Stamp Papers"],
    "Step 13": ["PDC Checklist as per Sanction", "Post-Dated Cheques", "ECS Mandate"],
    "Step 14": ["Disbursement Request", "Final Verification Report"],
}

_JOB_CODE_RE = re.compile(r"-F\d{2}[A-Z]-", re.IGNORECASE)
_EMPTY_STEP = {"", "pending", "na", "n/a", "-", "#ref!"}


@dataclass
class DashProject:
    """One project row from a dashboard tab."""

    job_code: str
    client_name: str
    bank_name: str
    project_name: str
    amount: float
    tl_name: str
    is_completed: bool
    step_cols: list[int]
    step_names: list[str]
    row: list[str]
    row_number: int
    source_tab: str


@dataclass
class StepProgress:
    completed: int
    applicable: int
    total: int
    percentage: int
    current_step: str = ""
    current_step_key: str = ""
    pending: list[tuple[str, str]] = field(default_factory=list)  # (name, "Step N")


def _cell(row: list[Any], idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) and row[idx] is not None else ""


def _float(value: str) -> float:
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip() or 0)
    except ValueError:
        return 0.0


def _parse_dash(values: list[list[str]], *, is_completed: bool, tab: str) -> list[DashProject]:
    step_cols = COMP_DASH_STEP_COLS if is_completed else NEW_DASH_STEP_COLS
    step_names = COMP_DASH_STEP_NAMES if is_completed else NEW_DASH_STEP_NAMES
    projects: list[DashProject] = []
    for i, row in enumerate(values):
        code = _cell(row, COL_JOB_CODE)
        if not code or not _JOB_CODE_RE.search(code):
            continue
        projects.append(
            DashProject(
                job_code=code,
                client_name=_cell(row, COL_CLIENT_NAME) or code,
                bank_name=_cell(row, COL_BANK_NAME) or "N/A",
                project_name=_cell(row, COL_PROJECT_NAME) or "N/A",
                amount=_float(_cell(row, COL_AMOUNT)),
                tl_name=_cell(row, COL_TL_NAME),
                is_completed=is_completed,
                step_cols=step_cols,
                step_names=step_names,
                row=row,
                row_number=i + 1,
                source_tab=tab,
            )
        )
    return projects


def calc_step_progress(project: DashProject) -> StepProgress:
    """Mirror file.gs calculateStepProgress: a step counts done if its cell has a
    real value (not blank/pending/NA)."""

    completed = 0
    pending: list[tuple[str, str]] = []
    current_step = ""
    current_key = ""
    for order, col in enumerate(project.step_cols):
        val = _cell(project.row, col).lower()
        if val and val not in _EMPTY_STEP:
            completed += 1
        else:
            name = project.step_names[order] if order < len(project.step_names) else f"Step {order + 1}"
            pending.append((name, f"Step {order + 1}"))
            if not current_step:
                current_step, current_key = name, f"Step {order + 1}"
    total = len(project.step_cols)
    applicable = total or 1
    return StepProgress(
        completed=completed,
        applicable=applicable,
        total=total,
        percentage=round(completed / applicable * 100),
        current_step=current_step,
        current_step_key=current_key,
        pending=pending,
    )


async def find_projects_by_code(client_code: str) -> list[DashProject]:
    """Return all ongoing + completed dashboard projects for a Client Job Code."""

    started = time.perf_counter()
    target = normalize_client_job_code(client_code)
    if not target:
        return []

    new_vals, comp_vals = await asyncio.gather(
        asyncio.to_thread(fetch_worksheet_values, NEW_DASH),
        asyncio.to_thread(fetch_worksheet_values, COMPLETED_DASH),
    )
    projects = _parse_dash(new_vals, is_completed=False, tab=NEW_DASH)
    projects += _parse_dash(comp_vals, is_completed=True, tab=COMPLETED_DASH)
    matched = [p for p in projects if normalize_client_job_code(p.job_code) == target]

    logger.info(
        "FMS v2 dash find_projects code=%s matched=%s latency_ms=%s",
        target,
        len(matched),
        int((time.perf_counter() - started) * 1000),
    )
    return matched


async def find_document_links(client_code: str) -> dict[str, str]:
    """Return document links for a code from RUF Help Sheet + Sanction Letter.

    RUF Help Sheet: [0]=code, [1]=Search, [2]=Valuation, [3]=TEV, [4]=DDR.
    Sanction Letter: [0]=code, [1]=bank, [2]=sanction link (verified indices).
    """

    target = normalize_client_job_code(client_code)
    if not target:
        return {}

    ruf_vals, sanction_vals = await asyncio.gather(
        asyncio.to_thread(fetch_worksheet_values, RUF_HELP_SHEET),
        asyncio.to_thread(fetch_worksheet_values, SANCTION_SHEET),
    )
    links: dict[str, str] = {}
    ruf_labels = {1: "Search Report", 2: "Valuation Report", 3: "TEV Report", 4: "DDR Report"}
    for row in ruf_vals[1:]:
        if normalize_client_job_code(_cell(row, 0)) == target:
            for idx, label in ruf_labels.items():
                link = _cell(row, idx)
                if link:
                    links[label] = _gdrive_download(link)
            break
    for row in sanction_vals[1:]:
        if normalize_client_job_code(_cell(row, 0)) == target:
            link = _cell(row, 2)
            if link:
                links["Sanction Letter"] = _gdrive_download(link)
            break
    return links


def _gdrive_download(url: str) -> str:
    """Convert a Google Drive share/open link to a direct download link."""

    if not url or "export=download" in url:
        return url
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url) or re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        return url
    file_id = match.group(1).rstrip("-")
    return f"https://drive.google.com/uc?id={file_id}&export=download"
