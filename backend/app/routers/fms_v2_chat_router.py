"""FMS v2 chat router."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.fms_v2.chat import chat_with_fms_v2
from app.fms_v2.models import FmsV2ChatMessage, FmsV2ChatResponse, TokenPayload
from app.utils.auth import get_current_user


router = APIRouter(prefix="/api/chat", tags=["FMS v2 Chat"])
logger = logging.getLogger("botivate_api.fms_v2.chat_router")


@router.post("/send", response_model=FmsV2ChatResponse)
async def send_message(
    data: FmsV2ChatMessage,
    user: TokenPayload = Depends(get_current_user),
):
    """Send a chat message to the FMS-only orchestration layer."""

    try:
        return await chat_with_fms_v2(data, user)
    except Exception as exc:
        logger.exception("FMS v2 chat request failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred. Please try again.",
        ) from exc
