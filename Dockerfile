FROM python:3.11-slim

WORKDIR /app/backend

# Build deps for any packages that compile (bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements from backend folder
COPY backend/requirements.txt .

# Install dependencies with UV (faster)
RUN pip install uv && uv pip install --system -r requirements.txt

# Copy backend code (app includes utils/, context/, etc.)
COPY backend/app ./app

# API-only backend. The frontend is deployed separately on Vercel (see
# frontend/) and reaches this service via VITE_API_BASE_URL — no static files
# are served here.

# Runtime dirs the app expects
RUN mkdir -p chroma_data uploads

# Expose port
EXPOSE 8000

# Run FastAPI
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
