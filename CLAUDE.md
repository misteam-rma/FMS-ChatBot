# CLAUDE.md

This file provides guidance to AI coding agents when working with this repository.
Read this file first before making changes.

---

## Project Overview

**RMA FMS v2 Chatbot** is a split-deploy app for Rahul Mishra & Associates.

- **Backend:** FastAPI API in `backend/`, deployed to Render with Docker.
- **Frontend:** React 19 + Vite + Tailwind v4 + shadcn/ui SPA in `frontend/`, deployed to Vercel.
- **Data source:** Google Sheets workbook `NEW-FMS-RMA` (ID `10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok`).
- **Chat/auth data:** `FMS1`-`FMS4` for chat records; `RAW DATA` for phone auth + admin phone lookups;
  `NEW DASH` / `Completed Dash` / `RUF Help Sheet` / `Sanction Letter` for the deterministic menu intents.
- **Client auth:** phone number **+** `Client Job Code` must pair in the same `RAW DATA` row (read-only).
- **Admin auth:** hard-coded `admin` / `admin123`.
- **Chat path:** deterministic Python sheet fetch/parsing first, then an LLM answer over structured records.
  Menu-button clicks are answered deterministically (no LLM); free-typed text goes to the LLM (a superset).

The active backend code is under `backend/app/fms_v2/` plus:

- `backend/app/routers/fms_v2_auth_router.py`
- `backend/app/routers/fms_v2_chat_router.py`
- `backend/app/main.py`

Legacy LangGraph, SQLite, adapter, approval, company, and old auth/chat backend files have been
removed from `backend/`. Keep new backend work inside the FMS v2 modules.

---

## Commands

Run backend commands from repo root unless noted.

```bash
# Backend local setup
cd backend
cp .env.example .env
pip install -r requirements.txt

# Run backend locally on port 8000
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run backend tests
pytest tests/ -v --tb=short

# Docker build + run, same context Render uses
docker build -t rma-fms-v2:latest .
docker run -p 8000:8000 --env-file .env rma-fms-v2:latest

# Frontend local setup
cd ../frontend
cp .env.example .env.local
npm install
npm run dev
npm run build
```

The backend test suite is under `backend/tests/`. Current tests cover smoke, FMS sheet parsing,
FMS auth, multi-provider LLM fallback, FMS chat orchestration, and Phase 5 no-SQLite guards.

---

## Environment

Backend env lives in `backend/.env.example`; frontend env lives in `frontend/.env.example`.
There is no canonical root `.env.example`.

### Backend

| Variable | Purpose |
|---|---|
| `APP_ENV` | `development` locally, `production` on Render |
| `ALLOWED_ORIGINS` | Comma-separated frontend origins; use explicit Vercel URL in production |
| `JWT_SECRET_KEY` | JWT signing secret; use 32+ random chars |
| `APP_SECRET_KEY` | Strong random app secret |
| `GOOGLE_SHEET_ID` | `10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full service-account JSON on one line |
| `CEREBRAS_API_KEY` / `CEREBRAS_MODEL` | First LLM provider; default model `gpt-oss-120b` |
| `GROQ_API_KEY` / `GROQ_MODEL` | Second LLM provider; default model `gpt-oss-120b` |
| `NVIDIA_API_KEY` / `NVIDIA_MODEL` | Third LLM provider; model must be set explicitly |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | Final fallback provider; default model `gpt-4o-mini` |

### Frontend

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | Backend API root, e.g. `https://<render-service>.onrender.com/api` |

Do not print or commit secret values.

---

## Active Architecture

```text
Vercel React SPA -> Render FastAPI -> input guard -> deterministic Google Sheets tools (FMS1-4 +
dashboard/RAW DATA/doc tabs) -> LLM answer over structured records (scope-limited, citation-guarded)
```

SQLite, SQLAlchemy sessions, company setup, ChromaDB, approval workflows, and the old LangGraph
agent are not part of the cleaned backend.

### Key Modules

| Area | Files | Notes |
|---|---|---|
| Config | `backend/app/config.py`, `backend/app/fms_v2/config.py` | FMS constants, provider config |
| Models | `backend/app/fms_v2/models.py` | Pydantic request/output/tool models; `_normalize_code` folds Unicode dashes |
| Sheets | `backend/app/fms_v2/sheets.py` | Google Sheets fetch (retry/timeout), parsing, generic `fetch_worksheet_values` |
| Auth sheet | `backend/app/fms_v2/auth_sheet.py` | Phone + code pairing check against `RAW DATA` |
| Dash | `backend/app/fms_v2/dash.py` | Dashboard readers (NEW DASH / Completed Dash) + document links (RUF / Sanction) |
| Intents | `backend/app/fms_v2/intents.py` | Deterministic menu intents (Status/Steps/Docs/Missing/Banks/FMS/Total…) |
| Admin sources | `backend/app/fms_v2/admin_sources.py` | Extra LLM records for admin (RAW DATA phones, dashboards) + document links |
| Tools | `backend/app/fms_v2/tools.py` | Validated tool wrapper for sheet fetch |
| LLM | `backend/app/fms_v2/llm.py` | Sequential fallback: Cerebras -> Groq -> NVIDIA -> OpenAI (12s each) |
| Chat | `backend/app/fms_v2/chat.py` | Auth routing, input guard, prompt builder, ranking, citation + scope-refusal guard |
| Health | `backend/app/fms_v2/health.py` | Service, Sheets, and provider health; database is `not_used` |
| Routers | `backend/app/routers/fms_v2_*_router.py` | Active auth/chat API (`/chat/send`, `/chat/intent`) |
| Frontend API | `frontend/src/lib/api.ts` | Calls `/auth/verify-client-code`, `/auth/verify-admin`, `/chat/send`, `/chat/intent` |

---

## Sheet Facts

Workbook: `NEW-FMS-RMA`

Workbook ID: `10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok`

Chat/parse sheets (`FMS_SHEET_NAMES`), header row 6:

| Sheet | Header row | Client Job Code column |
|---|---:|---|
| `FMS1` | 6 | `J` |
| `FMS2` | 6 | `B` |
| `FMS3` | 6 | `J` |
| `FMS4` | 6 | `B` |

Auxiliary tabs (in the same workbook — one workbook holds everything; verified against the
old 3-workbook Apps Script bot in `.agents/`):

| Tab | Used by | Key columns (0-based) |
|---|---|---|
| `RAW DATA` | phone auth, admin phone lookups | `[15]` Client Job Code, `[17]` Mobile Number, `[1]` Client Name |
| `NEW DASH` / `Completed Dash` | Status/Steps/Missing intents | data rows from ~row 11: `[1]` code, `[2]` name, `[3]` bank, `[5]` amount, `[8]` TL, `[28]` %, step cols `[9,10,15..23]` (NEW, 11 steps) / `..26` (Completed, 14) |
| `RUF Help Sheet` | FMS/Docs intent + doc queries | `[0]` code, `[1]` Search, `[2]` Valuation, `[3]` TEV, `[4]` DDR |
| `Sanction Letter` | FMS/Docs intent + doc queries | `[0]` code, `[1]` bank, `[2]` sanction link |

Some Client Job Codes appear in multiple rows and multiple sheets. Auth and chat context must
return all matching rows, not only the first row. Codes may contain Unicode dash variants
(non-breaking hyphen etc.); `normalize_client_job_code` folds them to ASCII `-` before matching.

Repeated workflow columns such as `Doer`, `Planned`, `Actual`, `URL`, `Remark`, and `Status`
must be preserved with step context by the parser.

---

## Auth Flow

Client (phone + code pairing):

1. Frontend posts `{phone, client_job_code}` to `POST /api/auth/verify-client-code`.
2. Backend verifies the phone and code appear in the **same `RAW DATA` row** (`auth_sheet.py`) — read-only.
3. It then confirms the code has FMS1-FMS4 records (data to serve).
4. Login fails if the pair is not found (codes with no phone on file cannot log in).
5. JWT includes `user_type="client"`, `client_job_code`, and the normalized `mobile_number`.

Admin:

1. Frontend posts to `POST /api/auth/verify-admin`.
2. Credentials are `admin` / `admin123`.
3. JWT includes `user_type="admin"`.

Rate limiting is still in-memory via `slowapi`; use Redis if deploying multiple instances.

---

## Chat Flow

Two endpoints in `fms_v2_chat_router.py`:

- `POST /api/chat/send` — free-typed messages → LLM path (a superset of the buttons).
- `POST /api/chat/intent` — menu-button clicks → deterministic dashboard answers, no LLM.
  Frontend routing: labels in `LABEL_TO_INTENT` hit `/intent`; anything else hits `/send`.

Input guard (runs first, before any fetch/LLM, in `screen_user_input`):

- Deterministic regex screen rejects injection ("ignore previous instructions", "reveal system
  prompt"), credential probes (service-account JSON, API key, JWT secret, `.env`), and write/
  mutation attempts. Blocked requests get an instant refusal — zero cost, no fetch, no LLM.

Client users:

- Can only access rows matching their JWT `client_job_code`; a query for another code is refused.
- Document/link queries ("sanction letter", "search report") pull `RUF Help Sheet` / `Sanction
  Letter` links into the LLM context so the LLM answers with real download links (superset of the FMS button).

Admin users:

- Can query across FMS1-FMS4, plus extra tabs when relevant (`admin_sources.py`): phone/contact
  queries pull `RAW DATA`; dashboard/count queries pull `NEW DASH` / `Completed Dash`.
- List/code summary queries are answered deterministically without the LLM.
- Broad queries fetch and rank parsed rows before passing capped structured context to the LLM.

LLM rules:

- Sheet access never happens inside prompts; the LLM receives structured records with source metadata.
- The system prompt is scope-limited: it refuses out-of-scope questions (general knowledge, coding,
  trivia) — such refusals bypass the citation guard (`is_scope_refusal`).
- Answers must cite sheet, row, and column for factual claims; the backend rejects uncited answers.
- Default answer style is Hinglish in Latin script, unless the user writes plain English or Devanagari.

---

## Deployment

Backend:

- Render Web Service
- Runtime: Docker
- Root Directory: `backend`
- Dockerfile: `backend/Dockerfile`
- Health endpoint: `/api/health`

Frontend:

- Vercel project
- Root Directory: `frontend`
- Framework: Vite
- Env: `VITE_API_BASE_URL=https://<render-service>.onrender.com/api`

Set `ALLOWED_ORIGINS` on Render to the exact Vercel origin. Do not use `*` in production.

See `docs/DEPLOYMENT.md` for step-by-step deployment.

---

## Verification

Before claiming backend work is complete, run:

```bash
cd backend
pytest tests/ -v --tb=short
```

For startup-specific changes, also boot the app or use a FastAPI `TestClient` with lifespan enabled.

Current smoke tests assert:

- app imports
- `/api/health` is registered
- health reports `database: "not_used"`
- FMS v2 auth and chat routers are active
- SQLite-backed company routes are not registered
