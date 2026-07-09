# Deployment Guide — RMA FMS v2 Chatbot

The app is a split deployment:

- **Frontend** (`frontend/`) — React 19 + Vite SPA on **Vercel**
- **Backend** (`backend/`) — FastAPI API on **Render** using Docker

The backend is API-only. It does not serve the SPA and it does not require SQLite
for normal startup, auth, chat, or health checks.

```text
Vercel React SPA -> Render FastAPI API -> Google Sheets FMS1-FMS4 -> LLM fallback
```

The frontend calls the backend through `VITE_API_BASE_URL`; the backend allows
that origin through `ALLOWED_ORIGINS`.

---

## Prerequisites

- GitHub repo connected to Render and Vercel
- Google service-account JSON with access to workbook `ChatBot-FMS-RMA`
- Google Sheet ID: `10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok`
- At least one configured LLM provider key:
  - Cerebras, Groq, NVIDIA, or OpenAI
- `JWT_SECRET_KEY`: generate with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 1. Backend -> Render

Render service settings:

| Setting | Value |
|---|---|
| Type | Web Service |
| Runtime | Docker |
| Branch | `main` or your deploy branch |
| Root Directory | `backend` |
| Dockerfile Path | `Dockerfile` |

Backend env vars, from `backend/.env.example`:

| Key | Value |
|---|---|
| `APP_ENV` | `production` |
| `ALLOWED_ORIGINS` | Vercel origin, set after frontend deploy |
| `JWT_SECRET_KEY` | 32+ random chars |
| `APP_SECRET_KEY` | strong random string |
| `GOOGLE_SHEET_ID` | `10Mf2nwiMSU0tqC1jBL-MaYIDAA1V1KuwuR_UugVhbok` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | full service-account JSON, single line |
| `CEREBRAS_API_KEY` | optional, first provider |
| `CEREBRAS_MODEL` | `gpt-oss-120b` |
| `GROQ_API_KEY` | optional, second provider |
| `GROQ_MODEL` | `gpt-oss-120b` |
| `NVIDIA_API_KEY` | optional, third provider |
| `NVIDIA_MODEL` | required only if using NVIDIA |
| `OPENAI_API_KEY` | optional but recommended final fallback |
| `OPENAI_MODEL` | `gpt-4o-mini` |

Provider fallback order is:

```text
Cerebras -> Groq -> NVIDIA -> OpenAI
```

Providers without both API key and model are skipped.

Deploy the backend and note the Render URL, for example:

```text
https://rma-fms-v2.onrender.com
```

Verify:

```text
https://<backend>/api/health
```

Expected health payload includes:

- `status`
- `service`
- `database: "not_used"`
- `google_sheets`
- `llm_providers`

Render free-tier note: cold starts can take 30-60 seconds after idle periods.

---

## 2. Frontend -> Vercel

Vercel project settings:

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Vite |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| Install Command | `npm install` |

Frontend env var, from `frontend/.env.example`:

| Key | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://<backend>.onrender.com/api` |

The `/api` suffix is required.

Deploy and note the Vercel URL, for example:

```text
https://rma-fms-v2.vercel.app
```

---

## 3. Close CORS

Back in Render, set:

```text
ALLOWED_ORIGINS=https://rma-fms-v2.vercel.app
```

Use the exact Vercel origin. Comma-separate multiple origins if needed.

Do not use `ALLOWED_ORIGINS=*` in production. The backend disables credentialed
CORS for wildcard origins and logs a warning outside development.

---

## 4. Verify End-to-End

1. Open the Vercel URL.
2. Client login:
   - Enter a valid `Client Job Code`.
   - Frontend should call `POST /api/auth/verify-client-code`.
   - Successful login routes to `/chat`.
3. Admin login:
   - Username: `admin`
   - Password: `admin123`
   - Frontend should call `POST /api/auth/verify-admin`.
4. Chat:
   - Frontend calls `POST /api/chat/send`.
   - Client answers must be scoped to the authenticated Client Job Code.
   - Factual answers should cite sheet, row, and column.

---

## Local Development

Backend:

```bash
cd backend
cp .env.example .env
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

With `VITE_API_BASE_URL` unset, the frontend dev server auto-targets
`http://localhost:8000/api`.

Run backend tests:

```bash
cd backend
pytest tests/ -v --tb=short
```

---

## Updating The Frontend

Vercel rebuilds from `frontend/` on push. There is no static build copy step and
no committed `backend/static/` output for the active deployment.

---

## Active API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | FMS v2 health: service, Sheets, LLM providers |
| `POST /api/auth/verify-client-code` | Client Job Code login |
| `POST /api/auth/verify-admin` | Hard-coded admin login |
| `POST /api/chat/send` | FMS v2 chat |

