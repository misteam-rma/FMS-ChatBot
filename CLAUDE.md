# CLAUDE.md

This file provides guidance to AI coding agents (Claude Code, Gemini, etc.) when working with this repository.
Read this file FIRST before making any changes.

---

## Project Overview

**Finance FMS Portal** (RMA — Rahul Mishra & Associates) — A FastAPI backend with a LangGraph agentic chatbot that lets CA firm employees and admins query a Finance FMS Google Sheets workbook (43 sheets, ~1500+ rows) via natural language chat. The frontend is a **React 19 + Vite + Tailwind v4 + shadcn/ui SPA** (source in `Replit-UI/`, pre-built into `backend/static/` and served by FastAPI).

**Domain**: Loan consultancy / DSA-style CA firm. Tracks client loan files from initial intake through a **19-step process** to final disbursement across multiple banks.

**Primary users**: Internal CA team members and clients querying in **English, Hinglish (Hindi-English mix), or Hindi** about client loan files, step statuses, pending tasks, queries, sanctions, and disbursements. The agent replies in the **same language/script** the user wrote in (see Language Handling below).

---

## Commands

All commands run from the `backend/` directory unless noted.

```bash
# Install dependencies
pip install -r backend/requirements.txt

# Run locally (port 8000)
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Reset database
cd backend
python reset_db.py

# Docker build + run (from backend/ — Render uses Root Directory = backend)
cd backend
docker build -t finance-fms:latest .
docker run -p 8000:8000 --env-file .env finance-fms:latest

# Run tests
cd backend && pytest tests/ -v --tb=short

# Rebuild the React frontend (from repo root) and sync into backend/static/
cd Replit-UI/artifacts/rma-portal
PORT=3000 BASE_PATH=/ pnpm run build      # PORT + BASE_PATH are required by vite.config.ts
cp -R dist/public/. ../../../backend/static/   # then commit backend/static/
```

> **Frontend build note**: `Replit-UI/` is a separate (gitignored) workspace —
> the React source. We do NOT build it in Docker/CI; instead we build locally
> and commit the output to `backend/static/`, which FastAPI serves directly
> (no Node in the image). After any UI change: rebuild, copy `dist/public/.`
> into `backend/static/` (removing old hashed `assets/index-*.js|css` first),
> and commit. Logos/favicon live in `Replit-UI/artifacts/rma-portal/public/`
> (so they survive rebuilds) and are also present in `backend/static/`.

A pytest suite exists under `backend/tests/` (smoke, auth, Hinglish, agent
utils). The smoke test boots the app and hits `/api/health`, so it catches
import errors and route-registration regressions. Ruff + pytest config live in
`backend/pyproject.toml`. You can also test manually via the frontend at
`http://localhost:8000` or hit the API directly (`/api/health`,
`/api/auth/verify-mobile`, `/api/chat/send`).

---

## Environment Setup

Copy `.env.example` to `.env` at the project root and fill in:

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required — chat completions and embeddings |
| `OPENAI_MODEL` | Default: `gpt-4o-mini` |
| `GOOGLE_SHEET_ID` | ID of the Finance FMS workbook (`1E6HKeoYxOrroF-ogNpmyks-nuYX4f-iUjFMG7Fu2X_4`) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service account JSON as a single line |
| `JWT_SECRET_KEY` | Token signing — use a 32+ char random string |
| `SKIP_GOOGLE_AUTH` | Set `false` for production |
| `APP_ENV` | Set to non-`development` in prod so `validate_production_secrets()` enforces non-default secrets |
| `ALLOWED_ORIGINS` | Comma-separated origin allow-list; leave `*` only in dev (credentials auto-disabled) |
| `FAST_LLM_API_KEY` | Optional — Cerebras/Groq fast inference for data answers. **Wired up** (`get_fast_llm()`); falls back to the primary model when unset |
| `FAST_LLM_MODEL` | Optional — model name on the fast provider (e.g. `llama-3.3-70b`) |
| `FAST_LLM_BASE_URL` | Optional — e.g. `https://api.groq.com/openai/v1` or `https://api.cerebras.ai/v1` |

Config loaded via Pydantic BaseSettings in `backend/app/config.py` — reads from `.env` or `../.env`.

---

## Architecture

```
React SPA (Vite, pre-built into backend/static) ↔ FastAPI ↔ SQLite + Google Sheets + ChromaDB + OpenAI
```

### Key Layers

| Layer | Files | Purpose |
|-------|-------|---------|
| **Routers** | `backend/app/routers/` | Thin HTTP handlers — `auth_router.py` (mobile verify → JWT), `chat_router.py` (message → agent), `company_router.py`, `approval_router.py` |
| **Agents** | `backend/app/agents/` | `hr_agent.py` (~1134 lines, LangGraph state machine) — intent classification, node routing, data queries, Hinglish aliases, hybrid LLM. `db_agent.py` — CRUD operations on sheets |
| **Adapters** | `backend/app/adapters/` | Factory pattern (`adapter_factory.py`) → `GoogleSheetsAdapter`. Pluggable to PostgreSQL/MongoDB/Supabase |
| **Services** | `backend/app/services/` | `auto_setup.py` (DB init), `rag_service.py` (ChromaDB policy search), `sheet_sync_service.py` (gspread R/W), `schema_analyzer.py` |
| **Database** | `backend/app/database.py` | Async SQLAlchemy + aiosqlite. Master DB: `botivate_master.db` |
| **Context** | `backend/app/context/` | `description.md` (~54KB, compressed) — dense schema reference for all 43 FMS sheets, injected into LLM prompts. Loaded once via `@lru_cache` in `get_workbook_context()`. Also `fms_step_reference.md` (compact step ref for intent classification) |
| **Frontend (source)** | `Replit-UI/artifacts/rma-portal/` | React 19 + Vite + Tailwind v4 + shadcn/ui SPA. Gitignored workspace; built locally, output committed to `backend/static/` |
| **Frontend (served)** | `backend/static/` | Pre-built Vite output (`index.html` + hashed `assets/`). FastAPI serves it with an SPA fallback route in `main.py` |

### Authentication Flow

1. User submits mobile number → `POST /api/auth/verify-mobile`
2. Router checks number against Google Sheets "RAW DATA" tab
3. On match, JWT issued with role (`employee` / `manager` / `hr` / `ceo` / `admin`)
4. Token sent in `Authorization: Bearer` header on subsequent requests

### Chat Flow

1. `chat_router.py` receives message, fetches all RAW DATA records from Google Sheets
2. Simple queries (greetings, "list all clients") handled directly in router. **"list all clients" deduplicates by client name** — RAW DATA has one row per loan *file* (client × bank × facility), so `format_client_list()` reports distinct clients + total loan files to avoid the "94 clients" confusion (there are ~15 distinct clients across ~94 files)
3. Complex queries → `chat_with_agent()` → LangGraph state machine in `hr_agent.py`
4. Intent classified → routed to handler node → data fetched from relevant sheets → LLM answers

### Language Handling

The agent replies in the **same language and SCRIPT** the user wrote in. The rule (in all three LLM response nodes: data-query, workbook-expert, general-handler in `hr_agent.py`) is **script-first**:

- Question in **Latin/Roman letters** → reply in Latin letters. **Never Devanagari** unless the question itself contains Devanagari characters.
  - Plain English → English only.
  - Romanized Hindi / Hinglish (e.g. "Mujhe mere clients ke baare mei batao") → Hinglish (still Latin letters).
- Question in **Devanagari** (e.g. "कितने क्लाइंट हैं") → Hindi in Devanagari.
- Default to English when unsure. Sheet/column names, Client Job Codes, and abbreviations stay as-is in every language.

> The earlier bug was vocabulary-based detection: a fully-Romanized Hindi sentence got classified as "Hindi" and answered in Devanagari. Script-first detection fixes this.

### Markdown / Links in answers

Chat answers are rendered via `marked` + `DOMPurify` (`Replit-UI/.../src/lib/markdown.ts`). Links open in a new tab (`target="_blank" rel="noopener noreferrer"` via a DOMPurify hook), GFM autolinking is on, and links are styled (`prose-a:*`). The data-query prompt instructs the LLM to emit **real Markdown links** `[Open](<url>)` for URL fields — never a bare "Open now" label with no href.

---

## Finance FMS Domain Context

### The 19-Step Loan Process

The workbook tracks a **19-step loan lifecycle** with 46 sub-tasks:

| Phase | Steps | Description |
|-------|-------|-------------|
| **Pre-sanction** | Step 1-8 | Checklists (P-CL, S-CL), document collection, set preparation, project report/CMA, board note (BN), legal search, valuation, TEV, DDR |
| **Mid-phase** | Step 9-15 | Board note/application, query handling, sanction letter |
| **Disbursement** | Step 16-19 | Documentation, pre-disbursement conditions (PDC), disbursement, post-disbursement |

### Key FMS Abbreviations

| Abbreviation | Full Name |
|-------------|-----------|
| P-CL | Primary Checklist |
| S-CL | Secondary Checklist |
| BN | Board Note |
| TEV | Techno-Economic Viability Report |
| DDR | Due Diligence Report |
| CMA | Credit Monitoring Arrangement / Project Report |
| PDC | Pre-Disbursement Condition |
| LAF | Loan Application Form |
| RM | Relationship Manager |

### Primary Key

**Client Job Code** — format: `[ABBREV]-[FY]-[TYPE][SEQ]` (e.g., `HOACPL-F25F-TL01`)
- `F25F` = FY 2025-26 Fresh, `F25E` = Enhancement, `F26F` = FY 2026-27
- `TL` = Term Loan, `CC` = Cash Credit, `BG` = Bank Guarantee

### 43 Sheets Organized by Function

1. **Client Intake**: RAW DATA, RAW DATA2, CODE MASTER, CLIENT DATA
2. **Process Definition**: Steps, StepMatrix, Steps Directory
3. **Task Execution**: DB_Format, DATA, Form responses 10, Doer Emails
4. **Team/Config**: TEAM MEMBER, TeamMatrix, Bank & Email ID, Config, Dash Help Sheet - DND
5. **Queries & Documents**: Query_Master, Form_Record_Responses, Form_Reply_Responses, Report Upload Form, RUF Help Sheet, Sanction Letter, Client Docs Index
6. **Dashboards**: FMS1-FMS4, NEW DASH, NEW DASH BANK, NEW DASH for pc, Status Dash, Post sanction, Completed Dash, Drop Dash, Agrasen Group, Manualy Status Dash
7. **Communication**: WhatsAppUsers, ChatMessages, Mail Log, Status Update
8. **System**: HELP_SHEET, Setup, Holidays

---

## Implementation Status

Phases 0–2, 4, 5 and most of Phase 3 are **implemented and verified** (see the
roadmap below). The items below are what remains.

### ✅ Resolved

- **Hinglish support** — `HINGLISH_ALIASES` (word-boundary safe) in `get_relevant_table_names()` ([hr_agent.py](backend/app/agents/hr_agent.py)).
- **Compact intent context** — `get_fms_step_reference()` injects [fms_step_reference.md](backend/app/context/fms_step_reference.md) instead of truncated description.md.
- **Default-secret startup guard** — `Settings.validate_production_secrets()` ([config.py](backend/app/config.py)), called in `lifespan()`.
- **Log redaction** — `password`/`mobile_number`/`access_token`/`token` redacted in `log_requests` middleware ([main.py](backend/app/main.py)).
- **Rate limiting** — `slowapi` 5/min on both auth endpoints ([auth_router.py](backend/app/routers/auth_router.py)). *In-memory storage* — resets per redeploy, not shared across instances; use Redis if scaling out.
- **Error sanitization** — chat router returns a generic 500 message.
- **Sheets caching + invalidation** — 3-min TTL on `get_all_records()`; all write paths call `invalidate_cache()` internally.
- **Adapter reuse** — singletons keyed by `(db_type, config, refresh_token)` ([adapter_factory.py](backend/app/adapters/adapter_factory.py)).
- **Inline import hack** — removed.
- **Test suite** — `pytest` suite under [backend/tests/](backend/tests) (smoke, auth, Hinglish, agent utils). Run `cd backend && pytest tests/ -v`.
- **Admin password hashing** — `verify_admin_password()` supports bcrypt hashes with a plaintext fallback for legacy sheet entries.
- **CORS** — credentials auto-disabled when `ALLOWED_ORIGINS="*"`; warns in non-dev.
- **Two startup bugs fixed** — missing imports (`Depends`/`AsyncSession`/`get_db`) in `main.py`, and `/api/health` shadowed by the static mount (health route now registered before the SPA fallback).
- **React SPA frontend** — Vite build served from `backend/static/` with an SPA fallback catch-all route; assets mounted under `/assets`. Logos + favicon wired (RMA branding); per-page titles (`RMA | Login` / `RMA | Admin` / `RMA | Client`).
- **Language: script-first detection** — English→English, Hinglish→Hinglish, Devanagari→Hindi across all three response nodes (see Language Handling).
- **Markdown links** — answers render clickable links (new tab, sanitized); LLM emits `[Open](<url>)` for URL fields.
- **`list all clients` dedup** — distinct clients vs. total loan files, no longer contradicts the agent's "distinct clients" answer.
- **Compressed + cached `description.md`** — 123KB → ~54KB (all 43 sheets now fit; the old 40KB truncation cut off ~25 sheets). `get_workbook_context()` is `@lru_cache`d (static file, read once; live sheet data is cached separately).

### ⚠️ Remaining technical debt

1. **`hr_agent.py` still ~1100 lines** — the Phase 3 module split (`state.py`, `utils.py`, `nodes/`) is **not done**. Lowest-risk remaining refactor.

2. **Admin passwords are plaintext in the sheet today** — hashing is *supported* but existing entries should be migrated to bcrypt hashes (`$2b$…`).

3. **Rate limiting is in-memory** — fine for a single Render instance; needs Redis backing for multi-instance/multi-worker deployments.

> ⚠️ **Verification honesty**: `Walkthrough.md` previously claimed verifications
> (429 rate limit, `/api/health`, redaction) that could not have run because the
> app failed to import. Two startup bugs were since fixed (missing imports in
> `main.py`; `/api/health` shadowed by the static mount). **Always actually boot
> the app and run `pytest` before claiming a check passed.** The smoke test in
> `tests/test_smoke.py` guards the import + health route specifically.

---

## Improvement Roadmap (Approved Direction)

Status: **Phase 0 ✅ · Phase 1 ✅ · Phase 2 ✅ · Phase 3 ⏳ (tests + linter + import done; module split pending) · Phase 4 ✅ · Phase 5 ✅**

### Phase 0: FMS Intelligence & Hinglish ⭐ HIGHEST PRIORITY ✅
- Create `backend/app/context/fms_step_reference.md` — compact 3KB step reference for prompt injection
- Add `HINGLISH_ALIASES` dict to `hr_agent.py` for Hinglish keyword expansion
- Replace `workbook_context[:2500]` with the compact step reference in intent prompt
- Add Hinglish examples to intent classification prompt
- Add Hinglish response-style matching to data answer prompt

### Phase 1: Security Hardening ✅
- ✅ `validate_production_secrets()` in config.py — refuses startup with default secrets
- ✅ Redact sensitive fields in request logging middleware
- ✅ `slowapi` rate limiting on auth endpoints (in-memory; Redis for multi-instance)
- ✅ Sanitize error messages in chat_router.py
- ✅ Admin password bcrypt verification with plaintext fallback (`verify_admin_password`)
- ✅ CORS credentials disabled when origins are `"*"`

### Phase 2: Performance & Latency ✅
- ✅ TTL cache (3min) on `GoogleSheetsAdapter.get_all_records()`
- ✅ Adapter instances cached in adapter_factory.py
- ✅ Pre-fetched data passed from chat_router to agent
- ✅ Hybrid LLM: `get_fast_llm()` (Cerebras/Groq) for data answers, primary for intent

### Phase 3: Code Quality ⏳
- ✅ Fix inline import hack in chat_router.py
- ⏳ **Split hr_agent.py into modules** (`state.py`, `utils.py`, `nodes/`) — NOT done
- ✅ pytest test suite (smoke, auth, agent utils, Hinglish)
- ✅ Ruff/pytest config in `backend/pyproject.toml`

### Phase 4: Frontend UX ✅
- ✅ Replaced vanilla-JS HTML with a React 19 + Vite + Tailwind v4 + shadcn/ui SPA (RMA branding)
- ✅ Markdown rendering (marked.js + DOMPurify) in chat bubbles, with clickable links (new tab, sanitized)
- ✅ Typing indicator wired up
- ✅ Chat history persistence (sessionStorage)
- ✅ Per-page titles + favicon; logos optimized (2MB → 42KB)

### Phase 5: DevOps ✅
- ✅ `/api/health` checks DB + Sheets connectivity (returns `degraded` on failure)
- ✅ Duplicate `backend/.env.example` removed (root `.env.example` is canonical)

---

## File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `backend/app/agents/hr_agent.py` | ~1134 | Core LangGraph agent — intent classification, data queries, all handler nodes |
| `backend/tests/` | — | pytest suite: `test_smoke.py`, `test_auth.py`, `test_hinglish.py`, `test_agent_utils.py`, `conftest.py` |
| `backend/app/agents/db_agent.py` | ~500 | CRUD operations on Google Sheets via adapter |
| `backend/app/routers/chat_router.py` | 234 | Chat endpoint — simple query handling + agent dispatch |
| `backend/app/routers/auth_router.py` | ~150 | Mobile number verification → JWT |
| `backend/app/config.py` | 61 | Pydantic BaseSettings — all env var loading |
| `backend/app/main.py` | 137 | FastAPI app, middleware, CORS, lifespan |
| `backend/app/adapters/google_sheets_adapter.py` | ~300 | Google Sheets read/write via gspread |
| `backend/app/adapters/adapter_factory.py` | ~50 | Factory pattern for database adapters |
| `backend/app/services/rag_service.py` | ~200 | ChromaDB policy embeddings and search |
| `backend/app/context/description.md` | ~54KB | Compressed schema reference for all 43 FMS sheets (was 123KB); injected into LLM prompts, `@lru_cache`d |
| `backend/app/context/fms_step_reference.md` | ~4KB | Compact 19-step reference for intent classification |
| `description.txt` | 875 | Repo-root copy — 41-sheet description (subset of description.md). Kept intentionally |
| `Replit-UI/artifacts/rma-portal/` | — | React SPA source (gitignored). Key files: `src/pages/{LoginPage,ChatPage}.tsx`, `src/lib/{api,markdown,storage}.ts`, `public/{RMA,RMA_Extended,favicon}.png` |
| `backend/static/` | — | Pre-built Vite output served by FastAPI (`index.html`, `assets/`, logos, favicon) |
| `backend/Dockerfile` | — | The backend deploy image (python:3.11-slim, API-only). Render builds it with Root Directory = `backend`. `backend/.dockerignore` scopes the context |

---

## Deployment (split: Render + Vercel)

The two halves deploy independently from this repo:

- **Backend → Render (Docker)**: build from `backend/Dockerfile` with the Render
  service **Root Directory = `backend`** (python:3.11-slim, API-only — does not
  serve the SPA). Set env vars in the Render dashboard (`backend/.env.example`):
  in prod `APP_ENV` ≠ `development`, `SKIP_GOOGLE_AUTH=false`, real
  `JWT_SECRET_KEY`/`OPENAI_API_KEY`/`GOOGLE_SERVICE_ACCOUNT_JSON`, and
  `ALLOWED_ORIGINS` = the Vercel URL.
- **Frontend → Vercel**: project **Root Directory = `frontend`** (Vite, auto-detected
  via `frontend/vercel.json`). Set `VITE_API_BASE_URL` = the Render URL **+ `/api`**.
- **CORS**: the backend disables credentialed CORS when `ALLOWED_ORIGINS=*`, so it
  must list the explicit Vercel origin.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for full step-by-step instructions and
[docs/COMMANDS.txt](docs/COMMANDS.txt) for a quick command reference.
