FROM python:3.11-slim

WORKDIR /app

# Build deps for any packages that compile (bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (paths are relative to the backend/ build context;
# on Render set the service Root Directory to `backend`).
COPY requirements.txt .
RUN pip install uv && uv pip install --system -r requirements.txt

# Copy backend code (app includes utils/, context/, etc.)
COPY app ./app

# API-only backend. The frontend is deployed separately on Vercel (see
# frontend/) and reaches this service via VITE_API_BASE_URL — no static files
# are served here.

# Runtime dirs the app expects
RUN mkdir -p chroma_data uploads

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
