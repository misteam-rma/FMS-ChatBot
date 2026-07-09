"""FMS v2 authentication router."""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, status

from app.fms_v2.config import ADMIN_PASSWORD, ADMIN_USERNAME
from app.fms_v2.models import (
    AdminLoginRequest,
    ClientCodeLoginRequest,
    FmsV2LoginResponse,
    FetchFmsRecordsInput,
)
from app.fms_v2.sheets import fetch_fms_records_by_client_code
from app.utils.auth import create_access_token
from app.utils.limiter import limiter


router = APIRouter(prefix="/api/auth", tags=["FMS v2 Authentication"])
logger = logging.getLogger("botivate_api.fms_v2.auth")


@router.post("/verify-client-code", response_model=FmsV2LoginResponse)
@limiter.limit("5/minute")
async def verify_client_code(request: Request, data: ClientCodeLoginRequest):
    """Authenticate a client by exact Client Job Code across FMS1-FMS4."""

    client_job_code = data.client_job_code
    logger.info("FMS v2 client auth start client_job_code=%s", client_job_code)

    result = await fetch_fms_records_by_client_code(
        FetchFmsRecordsInput(client_job_code=client_job_code)
    )
    if not result.ok:
        logger.error(
            "FMS v2 client auth sheet fetch failed client_job_code=%s errors=%s",
            client_job_code,
            [error.model_dump() for error in result.errors],
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Client-code authentication is temporarily unavailable.",
        )

    if not result.records:
        logger.warning("FMS v2 client auth rejected client_job_code=%s", client_job_code)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Client Job Code not found.",
        )

    employee_name = next(
        (record.client_name for record in result.records if record.client_name),
        "Client",
    )
    token_data = {
        "employee_id": result.client_job_code,
        "employee_name": employee_name,
        "mobile_number": "",
        "user_type": "client",
        "client_job_code": result.client_job_code,
    }
    access_token = create_access_token(token_data)

    logger.info(
        "FMS v2 client auth success client_job_code=%s records=%s",
        result.client_job_code,
        len(result.records),
    )

    return FmsV2LoginResponse(
        access_token=access_token,
        employee_id=result.client_job_code,
        employee_name=employee_name,
        user_type="client",
        client_job_code=result.client_job_code,
    )


@router.post("/verify-admin", response_model=FmsV2LoginResponse)
@limiter.limit("5/minute")
async def verify_admin(request: Request, data: AdminLoginRequest):
    """Authenticate the hard-coded admin user for the FMS-only backend."""

    username = str(data.username or "").strip()
    password = str(data.password or "")

    username_ok = secrets.compare_digest(username, ADMIN_USERNAME)
    password_ok = secrets.compare_digest(password, ADMIN_PASSWORD)
    if not (username_ok and password_ok):
        logger.warning("FMS v2 admin auth rejected username=%s", username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    token_data = {
        "employee_id": "admin",
        "employee_name": "Admin",
        "mobile_number": "",
        "user_type": "admin",
        "client_job_code": None,
    }
    access_token = create_access_token(token_data)
    logger.info("FMS v2 admin auth success")

    return FmsV2LoginResponse(
        access_token=access_token,
        employee_id="admin",
        employee_name="Admin",
        user_type="admin",
        client_job_code=None,
    )
