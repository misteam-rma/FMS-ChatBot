"""Deterministic menu intents for button-driven queries.

These mirror the RMA Apps Script bot (.agents/file.gs / file.html): a fixed set
of quick-reply actions (Status, Steps, Docs, Missing, Next Step, Banks, FMS,
Total, Contact, Profile, Menu) answered from the dashboard tabs without the LLM.

Free-typed messages are NOT routed here; they go to the FMS1-FMS4 LLM chat.
Output is clean markdown so the frontend markdown renderer shows tables/links.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.fms_v2.config import ADMIN_USERNAME
from app.fms_v2.dash import (
    STEP_DOCUMENTS,
    DashProject,
    calc_step_progress,
    find_document_links,
    find_projects_by_code,
)
from app.fms_v2.models import FmsV2ChatResponse, TokenPayload


logger = logging.getLogger("botivate_api.fms_v2.intents")

RMA_CONTACT_NUMBER = "916262345604"

# Canonical intent keys the UI buttons send.
INTENT_STATUS = "status"
INTENT_STEPS = "steps"
INTENT_DOCS = "docs"
INTENT_MISSING = "missing"
INTENT_NEXT_STEP = "next_step"
INTENT_BANKS = "banks"
INTENT_FMS = "fms"
INTENT_TOTAL = "total"
INTENT_CONTACT = "contact"
INTENT_PROFILE = "profile"
INTENT_MENU = "menu"

VALID_INTENTS = {
    INTENT_STATUS, INTENT_STEPS, INTENT_DOCS, INTENT_MISSING, INTENT_NEXT_STEP,
    INTENT_BANKS, INTENT_FMS, INTENT_TOTAL, INTENT_CONTACT, INTENT_PROFILE, INTENT_MENU,
}

# Default quick-reply buttons shown under most responses.
DEFAULT_REPLIES = ["Status", "Steps", "Docs", "Missing", "Banks", "Menu"]

DocLinksFn = Callable[[str], Awaitable[dict[str, str]]]
ProjectsFn = Callable[[str], Awaitable[list[DashProject]]]


def _reply(
    text: str,
    quick_replies: list[str] | None = None,
    *,
    cards: list[dict] | None = None,
    progress: int | None = None,
) -> FmsV2ChatResponse:
    """Build a response. `actions[0]` always carries quick_replies; optional
    `cards` / `progress` let the frontend render bank cards and progress bars
    (markdown `reply` stays as the text/fallback)."""

    action: dict = {"quick_replies": quick_replies or DEFAULT_REPLIES}
    if cards is not None:
        action["cards"] = cards
    if progress is not None:
        action["progress"] = progress
    return FmsV2ChatResponse(reply=text, actions=[action])


def _card(p: DashProject) -> dict:
    pr = calc_step_progress(p)
    return {
        "bank": p.bank_name,
        "amount": round(p.amount, 2),
        "progress": pr.percentage,
        "completed": pr.completed,
        "applicable": pr.applicable,
        "is_completed": p.is_completed,
    }


def _auth_code(user: TokenPayload) -> str:
    return (user.client_job_code or user.employee_id or "").strip()


async def handle_intent(
    intent: str,
    user: TokenPayload,
    *,
    projects_fn: ProjectsFn = find_projects_by_code,
    doc_links_fn: DocLinksFn = find_document_links,
) -> FmsV2ChatResponse:
    """Answer a menu intent deterministically from the dashboard tabs."""

    key = (intent or "").strip().lower().replace(" ", "_")
    if key not in VALID_INTENTS:
        return _reply("Yeh option samajh nahi aaya. Neeche diye buttons use karein.")

    logger.info("FMS v2 intent=%s user=%s", key, user.employee_id)

    if key == INTENT_MENU:
        return _menu(user)
    if key == INTENT_CONTACT:
        return _contact()

    role = (user.user_type or "").lower()
    code = _auth_code(user)
    if role == "admin" and not code:
        # Admin has no single code; menu intents are client-scoped by design.
        return _reply(
            "Admin menu intents ke liye ek Client Job Code chahiye. "
            "Aap type karke koi bhi code pooch sakte hain, ya button use karein."
        )

    if key == INTENT_FMS:
        return await _docs_links(code, doc_links_fn)

    projects = await projects_fn(code)
    if not projects:
        return _reply(
            f"`{code}` ke liye dashboard (NEW DASH / Completed Dash) mein koi project nahi mila.",
            ["FMS", "Menu"],
        )

    if key == INTENT_PROFILE:
        return _profile(user, projects)
    if key == INTENT_STATUS:
        return _status(projects)
    if key == INTENT_STEPS:
        return _steps(projects)
    if key == INTENT_BANKS:
        return _banks(projects)
    if key == INTENT_TOTAL:
        return _total(projects)
    if key == INTENT_MISSING:
        return _missing(projects)
    if key == INTENT_NEXT_STEP:
        return _next_step(projects)
    if key == INTENT_DOCS:
        return _docs_menu()
    return _menu(user)


# ---- formatters ------------------------------------------------------------

def _menu(user: TokenPayload) -> FmsV2ChatResponse:
    name = user.employee_name or user.client_job_code or user.employee_id or "there"
    text = (
        f"**RMA Finance Assistant**\n\n"
        f"Hi **{name}**, main aapke loan files track karne mein madad kar sakta hoon. "
        "Kisi bhi button par tap karein, ya seedha type karke sawaal poochein:\n\n"
        "- **Status** — project progress\n"
        "- **Steps** — step-by-step details\n"
        "- **Missing** — pending steps/docs\n"
        "- **Next Step** — next step ke documents\n"
        "- **Banks** — bank-wise info\n"
        "- **FMS** — Search/Valuation/TEV/DDR/Sanction links\n"
        "- **Total** — overview summary\n"
    )
    return _reply(text, ["Status", "Steps", "Missing", "Banks", "FMS", "Total"])


def _contact() -> FmsV2ChatResponse:
    text = (
        "**Contact & Support**\n\n"
        "RMA Finance\n\n"
        f"- WhatsApp: {RMA_CONTACT_NUMBER}\n"
        "- Timing: Mon-Sat, 10 AM - 7 PM"
    )
    return _reply(text, ["Status", "Menu"])


def _profile(user: TokenPayload, projects: list[DashProject]) -> FmsV2ChatResponse:
    code = _auth_code(user)
    client = projects[0].client_name if projects else (user.employee_name or code)
    text = (
        "**Your Profile**\n\n"
        f"- Client: **{client}**\n"
        f"- Code: **{code}**\n"
        f"- Role: **{user.user_type}**\n"
        f"- Projects: **{len(projects)}**"
    )
    return _reply(text, ["Status", "Menu"])


def _status(projects: list[DashProject]) -> FmsV2ChatResponse:
    if len(projects) == 1:
        return _single_status(projects[0])
    client = projects[0].client_name
    lines = [
        f"**Project Status — {client}** ({len(projects)} banks)",
        "",
        "| # | Bank | Amount (Cr) | Progress | Status |",
        "|---|---|---:|---:|---|",
    ]
    for i, p in enumerate(projects, start=1):
        pr = calc_step_progress(p)
        state = "Completed" if p.is_completed else "Ongoing"
        lines.append(f"| {i} | {_md(p.bank_name)} | {p.amount:.2f} | {pr.percentage}% | {state} |")
    lines.append("")
    lines.append(f"_Source: {_source(projects)}_")
    return _reply(
        "\n".join(lines),
        ["Steps", "Missing", "Banks", "Menu"],
        cards=[_card(p) for p in projects],
    )


def _single_status(p: DashProject) -> FmsV2ChatResponse:
    pr = calc_step_progress(p)
    state = "Completed" if p.is_completed else "Ongoing"
    text = (
        f"**Project Status — {p.client_name}**\n\n"
        f"- Code: **{p.job_code}**\n"
        f"- Bank: **{p.bank_name}**\n"
        f"- Project: {p.project_name}\n"
        f"- Amount: **Rs. {p.amount:.2f} Cr**\n"
        f"- Team Leader: {p.tl_name or 'N/A'}\n"
        f"- Progress: **{pr.percentage}%** ({pr.completed}/{pr.applicable} steps) — {state}\n"
    )
    if pr.current_step and not p.is_completed:
        text += f"- Current step: **{pr.current_step}**\n"
    text += f"\n_Source: {p.source_tab} row {p.row_number}._"
    return _reply(
        text, ["Steps", "Missing", "Next Step", "Menu"], progress=pr.percentage
    )


def _steps(projects: list[DashProject]) -> FmsV2ChatResponse:
    p = projects[0]
    pr = calc_step_progress(p)
    lines = [
        f"**Step Details — {p.bank_name}** ({pr.percentage}%)",
        "",
        "| Step | Status |",
        "|---|---|",
    ]
    for order, col in enumerate(p.step_cols):
        val = _cell_str(p, col)
        name = p.step_names[order] if order < len(p.step_names) else f"Step {order + 1}"
        done = bool(val) and val.lower() not in {"", "pending", "na", "n/a", "-", "#ref!"}
        mark = f"Done ({_md(val)})" if done else "Pending"
        lines.append(f"| {_md(name)} | {mark} |")
    lines.append("")
    lines.append(f"_Source: {p.source_tab} row {p.row_number}._")
    replies = ["Missing", "Next Step", "Menu"]
    if len(projects) > 1:
        lines.insert(1, f"\n_Showing bank 1 of {len(projects)}. Ask about a specific bank for others._")
    return _reply("\n".join(lines), replies, progress=pr.percentage)


def _banks(projects: list[DashProject]) -> FmsV2ChatResponse:
    lines = [
        "**Your Banks**",
        "",
        "| # | Bank | Amount (Cr) | Progress |",
        "|---|---|---:|---:|",
    ]
    for i, p in enumerate(projects, start=1):
        pr = calc_step_progress(p)
        lines.append(f"| {i} | {_md(p.bank_name)} | {p.amount:.2f} | {pr.percentage}% |")
    lines.append("")
    lines.append(f"_Source: {_source(projects)}_")
    return _reply(
        "\n".join(lines),
        ["Status", "Steps", "Menu"],
        cards=[_card(p) for p in projects],
    )


def _total(projects: list[DashProject]) -> FmsV2ChatResponse:
    total_amt = sum(p.amount for p in projects)
    completed = sum(1 for p in projects if p.is_completed)
    ongoing = len(projects) - completed
    text = (
        "**Total Overview**\n\n"
        f"- Banks: **{len(projects)}**\n"
        f"- Ongoing: **{ongoing}**\n"
        f"- Completed: **{completed}**\n"
        f"- Total Amount: **Rs. {total_amt:.2f} Cr**\n\n"
        f"_Source: {_source(projects)}_"
    )
    return _reply(text, ["Status", "Steps", "Menu"])


def _missing(projects: list[DashProject]) -> FmsV2ChatResponse:
    p = projects[0]
    pr = calc_step_progress(p)
    if not pr.pending:
        return _reply(
            f"**Missing Documents — {p.bank_name}**\n\nAll steps completed!\n\n"
            f"_Source: {p.source_tab} row {p.row_number}._",
            ["Status", "Menu"],
        )
    lines = [f"**Missing / Pending — {p.bank_name}** ({len(pr.pending)} pending)", ""]
    for name, key in pr.pending:
        lines.append(f"- **{name}**")
        for doc in STEP_DOCUMENTS.get(key, []):
            lines.append(f"    - {doc}")
    lines.append("")
    lines.append(f"_Source: {p.source_tab} row {p.row_number}._")
    return _reply("\n".join(lines), ["Next Step", "Steps", "Menu"])


def _next_step(projects: list[DashProject]) -> FmsV2ChatResponse:
    # Prefer the first ongoing project.
    target = next((p for p in projects if not p.is_completed), projects[0])
    pr = calc_step_progress(target)
    if not pr.current_step:
        return _reply(
            f"**Next Step — {target.bank_name}**\n\nAll steps completed!",
            ["Status", "Menu"],
        )
    lines = [
        f"**Next Step — {target.bank_name}**",
        "",
        f"Current pending step: **{pr.current_step}**",
        "",
    ]
    docs = STEP_DOCUMENTS.get(pr.current_step_key, [])
    if docs:
        lines.append("Documents needed:")
        lines.extend(f"- {doc}" for doc in docs)
    lines.append("")
    lines.append(f"_Source: {target.source_tab} row {target.row_number}._")
    return _reply("\n".join(lines), ["Missing", "Steps", "Menu"])


def _docs_menu() -> FmsV2ChatResponse:
    text = (
        "**Document Center**\n\n"
        "- **Missing** — pending step documents\n"
        "- **Next Step** — documents for the next step\n"
        "- **FMS** — Search/Valuation/TEV/DDR/Sanction links\n"
    )
    return _reply(text, ["Missing", "Next Step", "FMS", "Menu"])


async def _docs_links(code: str, doc_links_fn: DocLinksFn) -> FmsV2ChatResponse:
    links = await doc_links_fn(code)
    if not links:
        return _reply(
            f"`{code}` ke liye koi FMS document link nahi mila "
            "(RUF Help Sheet / Sanction Letter).",
            ["Status", "Menu"],
        )
    lines = [f"**FMS Documents — {code}**", ""]
    order = ["Search Report", "Valuation Report", "TEV Report", "DDR Report", "Sanction Letter"]
    for label in order:
        if label in links:
            lines.append(f"- **{label}**: [Download]({links[label]})")
    lines.append("")
    lines.append("_Source: RUF Help Sheet / Sanction Letter._")
    return _reply("\n".join(lines), ["Status", "Menu"])


# ---- helpers ---------------------------------------------------------------

def _cell_str(p: DashProject, idx: int) -> str:
    return str(p.row[idx]).strip() if idx < len(p.row) and p.row[idx] is not None else ""


def _md(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def _source(projects: list[DashProject]) -> str:
    tabs = sorted({p.source_tab for p in projects})
    return " / ".join(tabs)
