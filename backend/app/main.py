"""
Botivate HR Support - FastAPI Application Entry Point
Registers all routers, initializes DB, and starts the background scheduler.
"""

from contextlib import asynccontextmanager
import os
import time
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Set up logging for detailed backend tracking
logging.basicConfig(
    level=logging.INFO,
    format="\n%(asctime)s | BOTIVATE-BACKEND | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("botivate_api")

from app.config import settings
from app.database import init_db, async_session_factory
from app.routers.company_router import router as company_router
from app.routers.auth_router import router as auth_router
from app.routers.chat_router import router as chat_router
from app.routers.approval_router import router as approval_router, notifications_router
from app.services.approval_service import check_pending_reminders


# ── Background Scheduler (48h Reminders & 72h Escalation) ─

scheduler = AsyncIOScheduler()


async def reminder_job():
    """Runs every hour to check for overdue approvals."""
    async with async_session_factory() as db:
        result = await check_pending_reminders(db)
        if result["reminders_sent"] or result["escalations"]:
            print(f"[SCHEDULER] Reminders: {result['reminders_sent']}, Escalations: {result['escalations']}")


# ── App Lifespan ──────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables & start scheduler. Shutdown: stop scheduler."""
    await init_db()
    scheduler.add_job(reminder_job, "interval", hours=1)
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
                logger.info(f"   [PAYLOAD] {body.decode('utf-8')}")
            
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

ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register Routers ─────────────────────────────────────

app.include_router(company_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(approval_router)
app.include_router(notifications_router)


# ── Health Check ──────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "status": "running",
        "version": "1.0.0",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}
