"""
Botivate HR Support - FastAPI Application Entry Point
Registers all routers, initializes DB, and starts the background scheduler.
"""

from contextlib import asynccontextmanager
import os
import time
import logging
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
from app.database import init_db, async_session_factory, get_db
from app.routers.company_router import router as company_router
from app.routers.auth_router import router as auth_router
from app.routers.chat_router import router as chat_router
from app.services.auto_setup import auto_setup_database


# ── Background Scheduler ─

scheduler = AsyncIOScheduler()


# ── App Lifespan ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate secrets, create tables, auto-setup DB, start scheduler. Shutdown: stop scheduler."""
    settings.validate_production_secrets()
    await init_db()

    # Auto-setup database on first startup
    async with async_session_factory() as session:
        await auto_setup_database(session)

    scheduler.start()
    print(f"🚀 {settings.app_name} is running!")
    yield
    scheduler.shutdown()


# ── Create FastAPI App ────────────────────────────────────

app = FastAPI(
    title=settings.app_name,
    description="Agentic AI-powered HR Support System - Fully Dynamic, Multi-Company",
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
                    for key in ["password", "mobile_number", "access_token", "token"]:
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

app.include_router(company_router)
app.include_router(auth_router)
app.include_router(chat_router)

# ── Health Check ──────────────────────────────────────────

@app.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import text, select
    from app.models.models import DatabaseConnection
    from app.adapters.adapter_factory import get_adapter

    health_status = {
        "status": "healthy",
        "database": "untested",
        "google_sheets": "untested"
    }
    
    # Check SQLite database
    try:
        await db.execute(text("SELECT 1"))
        health_status["database"] = "healthy"
    except Exception as e:
        logger.error(f"Health check DB error: {e}")
        health_status["database"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"
        
    # Check Google Sheets Connection
    try:
        result = await db.execute(
            select(DatabaseConnection).where(
                DatabaseConnection.is_active == True
            )
        )
        db_conn = result.scalars().first()
        if db_conn:
            adapter = await get_adapter(db_conn.db_type, db_conn.connection_config)
            if hasattr(adapter, "spreadsheet") and adapter.spreadsheet:
                health_status["google_sheets"] = "healthy"
            else:
                health_status["google_sheets"] = "disconnected"
                health_status["status"] = "degraded"
        else:
            health_status["google_sheets"] = "no_active_connection"
    except Exception as e:
        logger.error(f"Health check Sheets error: {e}")
        health_status["google_sheets"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"

    return health_status


# ── API-only backend ──────────────────────────────────────
# The frontend is deployed separately on Vercel (see frontend/) and reaches
# this service via VITE_API_BASE_URL. This backend serves the API only and no
# longer hosts the React SPA. Set ALLOWED_ORIGINS to the Vercel domain(s) so
# the browser can make credentialed cross-origin requests.


@app.get("/")
async def root():
    return {"service": settings.app_name, "status": "ok", "docs": "/docs"}
