# Deployment Guide — RMA Finance FMS Portal

The app is a **split deployment**:

- **Frontend** (`frontend/`) — React 19 + Vite SPA → **Vercel**
- **Backend** (`backend/`) — FastAPI + LangGraph agent → **Render** (Docker)

They run on different origins and talk over HTTPS: the frontend calls the
backend via `VITE_API_BASE_URL`, and the backend allows that origin via
`ALLOWED_ORIGINS` (CORS).

```
Vercel (React SPA)  ──HTTPS /api/*──▶  Render (FastAPI)  ──▶  Google Sheets + OpenAI + SQLite
```

---

## Prerequisites

- GitHub repo connected to both Vercel and Render
- **OpenAI API key**
- **Google service-account JSON** (Google Cloud → Service Accounts) with access to the workbook
- **Google Sheet ID** — the `{ID}` in `https://docs.google.com/spreadsheets/d/{ID}/edit`
- A `JWT_SECRET_KEY` — generate with: `python -c "import secrets; print(secrets.token_hex(32))"`

---

## 1. Backend → Render (Docker)

Render builds from **`backend/Dockerfile`** (python:3.11-slim, API-only — it no
longer serves the SPA).

**Service settings**

| Setting | Value |
|---------|-------|
| Type | Web Service |
| Runtime | **Docker** |
| Branch | `main` (or your deploy branch) |
| **Root Directory** | **`backend`** — the build context is the backend folder |
| Dockerfile Path | `Dockerfile` (i.e. `backend/Dockerfile`) |

**Environment variables** (see `backend/.env.example`)

| Key | Value |
|-----|-------|
| `OPENAI_API_KEY` | your key |
| `OPENAI_MODEL` | `gpt-4o-mini` |
| `JWT_SECRET_KEY` | 32+ random chars |
| `APP_SECRET_KEY` | strong random string |
| `APP_ENV` | `production` |
| `GOOGLE_SHEET_ID` | `1E6HKeoYxOrroF-ogNpmyks-nuYX4f-iUjFMG7Fu2X_4` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | full JSON, single line |
| `GOOGLE_EMPLOYEE_SHEET_NAME` | `RAW DATA` |
| `SKIP_GOOGLE_AUTH` | `false` |
| `ALLOWED_ORIGINS` | set in step 3 (the Vercel URL) |
| `FAST_LLM_API_KEY` / `FAST_LLM_MODEL` / `FAST_LLM_BASE_URL` | optional (Groq/Cerebras) |

Deploy, then note the URL (e.g. `https://finance-chatbot-9ni2.onrender.com`).
Verify: `https://<backend>/api/health` returns JSON with a `status` field.

> **Free-tier note:** Render free instances sleep when idle; the first request
> after a cold start takes ~30–60s. Login will appear to hang once, then work.

---

## 2. Frontend → Vercel

**Project settings** (New Project → import the repo)

| Setting | Value |
|---------|-------|
| Root Directory | **`frontend`** ← the key setting |
| Framework Preset | Vite (auto-detected via `frontend/vercel.json`) |
| Build Command | `npm run build` (auto) |
| Output Directory | `dist` (auto) |
| Install Command | `npm install` (auto) |

**Environment variable** (see `frontend/.env.example`)

| Key | Value |
|-----|-------|
| `VITE_API_BASE_URL` | `https://<backend>.onrender.com/api` ← backend URL **+ `/api`** |

Deploy, then note the Vercel URL (e.g. `https://rma-frontend.vercel.app`).

> The `/api` suffix is required. Set the var for **Production** (and Preview if
> you want PR previews to reach the backend).

---

## 3. Close the CORS loop (do last)

Back on **Render**, set `ALLOWED_ORIGINS` to the Vercel domain and redeploy:

```
ALLOWED_ORIGINS=https://rma-frontend.vercel.app
```

This is mandatory: the backend **disables credentialed CORS when
`ALLOWED_ORIGINS=*`** (browsers forbid `*` + credentials), so it must be the
explicit Vercel origin. Comma-separate multiple origins.

---

## 4. Verify end-to-end

1. Open the Vercel URL — login page loads (RMA logo, favicon, title "RMA | Login").
2. Log in with a registered mobile number — routes to `/chat`.
3. DevTools → Network: `verify-mobile` / `chat/send` hit the Render `/api/...`
   with **200** and no CORS error.

---

## Local development

Run the two halves in separate terminals.

**Backend** (port 8000):
```bash
cd backend
cp .env.example .env          # fill in real values
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend** (port 5173):
```bash
cd frontend
npm install
npm run dev
```

With `VITE_API_BASE_URL` unset, the dev server auto-targets the backend on
`http://localhost:8000/api`. Run the backend tests with `cd backend && pytest tests/ -v`.

---

## Updating the frontend

Vercel rebuilds on every push to the deploy branch — just commit `frontend/`
source changes and push. There is **no** "build locally and commit static"
step anymore (that was the old single-service setup).

See [CLAUDE.md](../CLAUDE.md) for full architecture and the env-var reference.
