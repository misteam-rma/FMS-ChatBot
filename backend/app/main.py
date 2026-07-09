"""
Botivate HR Support - FastAPI Application Entry Point
Registers the FMS v2 API routers.
"""

from contextlib import asynccontextmanager
import os
import time
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.utils.limiter import limiter

# Set up logging for detailed backend tracking
logging.basicConfig(
    level=logging.INFO,
    format="\n%(asctime)s | BOTIVATE-BACKEND | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("botivate_api")

from app.config import settings
from app.fms_v2.health import check_fms_v2_health
from app.routers.fms_v2_auth_router import router as auth_router
from app.routers.fms_v2_chat_router import router as chat_router


HealthChecker = Callable[[], Awaitable[dict[str, Any]]]


# ── App Lifespan ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate secrets. FMS v2 does not initialize SQLite."""
    settings.validate_production_secrets()
    print(f"🚀 {settings.app_name} FMS v2 API is running!")
    yield


# ── Create FastAPI App ────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    description="FMS v2 sheet-backed chatbot API",
    version="1.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # 1. Log Incoming Request Details
    client_ip = request.client.host if request.client else "Unknown"
    logger.info(f"➡️ [NEW REQUEST] {request.method} {request.url.path} from IP: {client_ip}")
    
    if request.query_params:
        logger.info(f"   [QUERY] {request.query_params}")
    
    # 2. Extract and Log Body if JSON (to not block file uploads like PDFs/CSV)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.body()
            if body:
                body_str = body.decode('utf-8')
                try:
                    import json
                    body_json = json.loads(body_str)
                    redacted = False
                    for key in ["password", "mobile_number", "client_job_code", "access_token", "token"]:
                        if key in body_json:
                            body_json[key] = "***REDACTED***"
                            redacted = True
                    if redacted:
                        body_str = json.dumps(body_json)
                except Exception:
                    pass
                logger.info(f"   [PAYLOAD] {body_str}")
            
            # Put the body back so route handler can read it
            async def receive():
                return {"type": "http.request", "body": body}
            request._receive = receive
        except Exception as e:
            logger.warning(f"   [PAYLOAD ERROR] Failed to read body: {e}")

    # 3. Process the Route Logic
    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        status_code = response.status_code
        
        # 4. Log Response Status
        if 200 <= status_code < 300:
            logger.info(f"✅ [SUCCESS] Returned {status_code} in {process_time:.2f}ms")
        elif 400 <= status_code < 500:
            logger.warning(f"⚠️ [CLIENT EXCEPTION] Returned {status_code} in {process_time:.2f}ms")
        else:
            logger.error(f"❌ [SERVER FAULT] Returned {status_code} in {process_time:.2f}ms")
            
        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        logger.error(f"🔥 [CRITICAL EXCEPTION] {str(e)} | Time elapsed: {process_time:.2f}ms")
        raise e

# ── CORS ──────────────────────────────────────────────────
# Browsers reject the combination of allow_origins=["*"] with
# allow_credentials=True. So when origins are an explicit allow-list we enable
# credentials; when left at the "*" default we must turn credentials off.

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",")]
_wildcard_origins = ALLOWED_ORIGINS == ["*"]

if _wildcard_origins and settings.app_env.lower() != "development":
    logger.warning(
        "⚠️ ALLOWED_ORIGINS is '*' in a non-development environment. "
        "Set an explicit comma-separated origin list to allow credentialed requests."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=not _wildcard_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register Routers ─────────────────────────────────────

app.include_router(auth_router)
app.include_router(chat_router)

# ── Health Check ──────────────────────────────────────────

def get_health_checker() -> HealthChecker:
    return check_fms_v2_health


@app.get("/api/health")
async def health(checker: HealthChecker = Depends(get_health_checker)):
    return await checker()


# ── API-only backend ──────────────────────────────────────
# The frontend is deployed separately on Vercel (see frontend/) and reaches
# this service via VITE_API_BASE_URL. This backend serves the API only and no
# longer hosts the React SPA. Set ALLOWED_ORIGINS to the Vercel domain(s) so
# the browser can make credentialed cross-origin requests.


@app.get("/")
async def root():
    return {"service": settings.app_name, "status": "ok", "docs": "/docs"}
