FROM python:3.10-slim

WORKDIR /app/backend

# Copy requirements from backend folder
COPY backend/requirements.txt .

# Install dependencies with UV (faster)
RUN pip install uv && uv pip install -r requirements.txt

# Copy backend code
COPY backend/app ./app
COPY backend/static ./static

# Expose port
EXPOSE 8000

# Run FastAPI
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
