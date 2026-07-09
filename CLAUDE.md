# CLAUDE.md

This file provides guidance to AI coding agents when working with this repository.
Read this file first before making changes.

---

## Project Overview

**RMA FMS v2 Chatbot** is a split-deploy app for Rahul Mishra & Associates.

- **Backend:** FastAPI API in `backend/`, deployed to Render with Docker.
- **Frontend:** React 19 + Vite + Tailwind v4 + shadcn/ui SPA in `frontend/`, deployed to Vercel.
- **Data source:** Google Sheets workbook `ChatBot-FMS-RMA`.
- **Active sheets:** only `FMS1`, `FMS2`, `FMS3`, and `FMS4`.
- **Client auth:** `Client Job Code`, validated from FMS1-FMS4.
- **Admin auth:** hard-coded `admin` / `admin123`.
- **Chat path:** deterministic Python sheet fetch/parsing first, then an LLM answer over structured records.

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
Vercel React SPA -> Render FastAPI -> deterministic FMS1-FMS4 Google Sheets tools -> LLM fallback
```

SQLite, SQLAlchemy sessions, company setup, ChromaDB, approval workflows, and the old LangGraph
agent are not part of the cleaned backend.

### Key Modules

| Area | Files | Notes |
|---|---|---|
| Config | `backend/app/config.py`, `backend/app/fms_v2/config.py` | FMS constants, provider config |
| Models | `backend/app/fms_v2/models.py` | Pydantic request/output/tool models |
| Sheets | `backend/app/fms_v2/sheets.py` | Deterministic Google Sheets fetch, retry, timeout, parsing |
| Tools | `backend/app/fms_v2/tools.py` | Validated tool wrapper for sheet fetch |
| LLM | `backend/app/fms_v2/llm.py` | Sequential fallback: Cerebras -> Groq -> NVIDIA -> OpenAI |
| Chat | `backend/app/fms_v2/chat.py` | Authorization, prompt builder, admin narrowing, citation guard |
| Health | `backend/app/fms_v2/health.py` | Service, Sheets, and provider health; database is `not_used` |
| Routers | `backend/app/routers/fms_v2_*_router.py` | Active auth/chat API |
| Frontend API | `frontend/src/lib/api.ts` | Calls `/auth/verify-client-code`, `/auth/verify-admin`, `/chat/send` |

---

## Sheet Facts

Workbook: `ChatBot-FMS-RMA`

Workbook ID: `10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok`

| Sheet | GID | Header row | Client Job Code column |
|---|---:|---:|---|
| `FMS1` | `663292535` | 6 | `J` |
| `FMS2` | `315694386` | 6 | `B` |
| `FMS3` | `1157508021` | 6 | `J` |
| `FMS4` | `486978298` | 6 | `B` |

Some Client Job Codes appear in multiple rows and multiple sheets. Auth and chat context must
return all matching rows, not only the first row.

Repeated workflow columns such as `Doer`, `Planned`, `Actual`, `URL`, `Remark`, and `Status`
must be preserved with step context by the parser.

---

## Auth Flow

Client:

1. Frontend posts `client_job_code` to `POST /api/auth/verify-client-code`.
2. Backend searches exact normalized `Client Job Code` matches across FMS1-FMS4.
3. Login succeeds if at least one matching row exists.
4. JWT includes `user_type="client"` and `client_job_code`.

Admin:

1. Frontend posts to `POST /api/auth/verify-admin`.
2. Credentials are `admin` / `admin123`.
3. JWT includes `user_type="admin"`.

Rate limiting is still in-memory via `slowapi`; use Redis if deploying multiple instances.

---

## Chat Flow

`POST /api/chat/send` uses `fms_v2_chat_router.py`.

Client users:

- Can only access rows matching their JWT `client_job_code`.
- If the message asks for another Client Job Code, the backend refuses before fetching or calling the LLM.

Admin users:

- Can query across FMS1-FMS4.
- If a query includes Client Job Codes, deterministic fetch runs for those codes.
- List/code summary queries are answered deterministically without the LLM.
- Broad queries fetch and rank parsed FMS rows before passing capped structured context to the LLM.

LLM rules:

- Sheet access never happens inside prompts.
- The LLM receives structured records with source metadata.
- Answers must cite sheet, row, and column for factual claims.
- Backend rejects uncited LLM answers.
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
