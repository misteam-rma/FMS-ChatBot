"""Tests for deterministic menu intents (dashboard-backed, no LLM)."""

import pytest

from app.fms_v2.dash import DashProject, calc_step_progress
from app.fms_v2.intents import handle_intent
from app.fms_v2.models import TokenPayload


def client_user(code="SIPL-F25F-TL04"):
    return TokenPayload(
        employee_id=code, employee_name="Sarthak Ispat",
        user_type="client", client_job_code=code,
    )


def ongoing_project(code="SIPL-F25F-TL04", bank="Bank of Baroda", pct_row=None):
    # 34-wide row; step cols 9,10,15..23. Put "Done" in the first few steps.
    row = [""] * 34
    row[1], row[2], row[3], row[4], row[5], row[8] = (
        code, "Sarthak Ispat", bank, "Fresh TI", "48.00", "CA Danesh",
    )
    row[9] = "20/06"   # step 1 done
    row[10] = "21/06"  # step 2 done
    # steps 3-11 (cols 15..23) left blank => pending
    return DashProject(
        job_code=code, client_name="Sarthak Ispat", bank_name=bank,
        project_name="Fresh TI", amount=48.0, tl_name="CA Danesh",
        is_completed=False,
        step_cols=[9, 10, 15, 16, 17, 18, 19, 20, 21, 22, 23],
        step_names=[f"Step {i}: X" for i in range(1, 12)],
        row=row, row_number=11, source_tab="NEW DASH",
    )


async def fake_projects(_code):
    return [ongoing_project()]


async def fake_no_projects(_code):
    return []


async def fake_docs(_code):
    return {
        "Search Report": "https://drive.google.com/uc?id=abc&export=download",
        "Sanction Letter": "https://drive.google.com/uc?id=xyz&export=download",
    }


def _buttons(resp):
    return resp.actions[0]["quick_replies"] if resp.actions else []


def test_step_progress_counts_done_and_pending():
    p = ongoing_project()
    pr = calc_step_progress(p)
    assert pr.completed == 2
    assert pr.total == 11
    assert pr.percentage == 18
    assert pr.current_step.startswith("Step 3")
    assert len(pr.pending) == 9


@pytest.mark.asyncio
async def test_status_intent_single_project():
    resp = await handle_intent("status", client_user(), projects_fn=fake_projects)
    assert "Project Status" in resp.reply
    assert "Bank of Baroda" in resp.reply
    assert "18%" in resp.reply
    assert "Source: NEW DASH row 11" in resp.reply
    assert "Steps" in _buttons(resp)


@pytest.mark.asyncio
async def test_steps_intent_renders_markdown_table():
    resp = await handle_intent("steps", client_user(), projects_fn=fake_projects)
    assert "| Step | Status |" in resp.reply
    assert "|---|---|" in resp.reply
    assert "Pending" in resp.reply
    assert "Done" in resp.reply


@pytest.mark.asyncio
async def test_missing_intent_lists_pending_with_docs():
    resp = await handle_intent("missing", client_user(), projects_fn=fake_projects)
    assert "pending" in resp.reply.lower()
    # step 3 pending => docs from STEP_DOCUMENTS["Step 3"]
    assert "Loan Application Form" in resp.reply


@pytest.mark.asyncio
async def test_next_step_intent_shows_docs_for_current_step():
    resp = await handle_intent("next_step", client_user(), projects_fn=fake_projects)
    assert "Next Step" in resp.reply
    assert "Documents needed" in resp.reply


@pytest.mark.asyncio
async def test_total_and_banks_intents():
    total = await handle_intent("total", client_user(), projects_fn=fake_projects)
    assert "Total Overview" in total.reply
    assert "48.00" in total.reply
    banks = await handle_intent("banks", client_user(), projects_fn=fake_projects)
    assert "Your Banks" in banks.reply


@pytest.mark.asyncio
async def test_fms_intent_returns_document_links():
    resp = await handle_intent("fms", client_user(), doc_links_fn=fake_docs)
    assert "FMS Documents" in resp.reply
    assert "[Download](https://drive.google.com/uc?id=abc&export=download)" in resp.reply
    assert "Sanction Letter" in resp.reply


@pytest.mark.asyncio
async def test_menu_and_contact_need_no_data():
    menu = await handle_intent("menu", client_user(), projects_fn=fake_no_projects)
    assert "RMA Finance Assistant" in menu.reply
    contact = await handle_intent("contact", client_user(), projects_fn=fake_no_projects)
    assert "Contact" in contact.reply


@pytest.mark.asyncio
async def test_unknown_intent_is_rejected():
    resp = await handle_intent("launch_rockets", client_user(), projects_fn=fake_projects)
    assert "samajh nahi" in resp.reply.lower()


@pytest.mark.asyncio
async def test_no_projects_message():
    resp = await handle_intent("status", client_user(), projects_fn=fake_no_projects)
    assert "koi project nahi mila" in resp.reply