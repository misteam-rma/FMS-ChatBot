"""LLM provider fallback boundary for FMS v2."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

from app.fms_v2.config import (
    DEFAULT_LLM_PROVIDER_ORDER,
    LlmProviderName,
    LlmProviderSettings,
    get_llm_provider_settings,
)


logger = logging.getLogger("botivate_api.fms_v2.llm")

ChatCompletionCaller = Callable[
    [LlmProviderSettings, "GenerateFmsAnswerInput"],
    str | Awaitable[str],
]


class LlmMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=80000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("message content cannot be empty")
        return cleaned


class GenerateFmsAnswerInput(BaseModel):
    messages: list[LlmMessage] = Field(..., min_length=1, max_length=30)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1200, ge=1, le=4096)
    provider_order: list[LlmProviderName] = Field(
        default_factory=lambda: list(DEFAULT_LLM_PROVIDER_ORDER),
        min_length=1,
        max_length=4,
    )

    @field_validator("provider_order")
    @classmethod
    def reject_duplicate_providers(
        cls, value: list[LlmProviderName]
    ) -> list[LlmProviderName]:
        if len(value) != len(set(value)):
            raise ValueError("provider_order cannot contain duplicates")
        return value


class LlmResult(BaseModel):
    ok: bool
    provider: str = ""
    model: str = ""
    content: str = ""
    latency_ms: int = Field(default=0, ge=0)
    error: str | None = None


async def generate_fms_answer(
    data: GenerateFmsAnswerInput | dict[str, Any],
    *,
    providers: Sequence[LlmProviderSettings] | None = None,
    caller: ChatCompletionCaller | None = None,
) -> LlmResult:
    """Generate an answer using sequential provider fallback.

    The caller receives only already-built messages. Sheet access and parsing
    must happen before this boundary in deterministic Python code.
    """

    started = time.perf_counter()
    request = GenerateFmsAnswerInput.model_validate(data)
    candidates = _ordered_providers(
        providers if providers is not None else get_llm_provider_settings(),
        request.provider_order,
    )
    errors: list[str] = []

    logger.info(
        "FMS v2 LLM generation start providers=%s messages=%s max_tokens=%s",
        ",".join(request.provider_order),
        len(request.messages),
        request.max_tokens,
    )

    if not candidates:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.error("FMS v2 LLM generation failed: no providers configured")
        return LlmResult(
            ok=False,
            latency_ms=latency_ms,
            error="No LLM providers are configured.",
        )

    attempted_provider = False
    for provider in candidates:
        provider_started = time.perf_counter()
        if not provider.is_configured:
            logger.info(
                "FMS v2 LLM provider skipped provider=%s reason=missing_key_or_model",
                provider.name,
            )
            continue

        attempted_provider = True
        logger.info(
            "FMS v2 LLM provider call start provider=%s model=%s",
            provider.name,
            provider.model,
        )

        try:
            content = await asyncio.wait_for(
                _call_provider(provider, request, caller=caller),
                timeout=provider.timeout_seconds,
            )
            provider_latency_ms = int((time.perf_counter() - provider_started) * 1000)
            total_latency_ms = int((time.perf_counter() - started) * 1000)
            logger.info(
                "FMS v2 LLM provider call success provider=%s model=%s latency_ms=%s",
                provider.name,
                provider.model,
                provider_latency_ms,
            )
            return LlmResult(
                ok=True,
                provider=provider.name,
                model=provider.model,
                content=content,
                latency_ms=total_latency_ms,
            )
        except Exception as exc:
            provider_latency_ms = int((time.perf_counter() - provider_started) * 1000)
            message = f"{provider.name}: {exc}"
            errors.append(message)
            logger.warning(
                "FMS v2 LLM provider call failed provider=%s model=%s latency_ms=%s error=%s",
                provider.name,
                provider.model,
                provider_latency_ms,
                exc,
            )

    latency_ms = int((time.perf_counter() - started) * 1000)
    if not attempted_provider:
        logger.error("FMS v2 LLM generation failed: no providers configured")
        return LlmResult(
            ok=False,
            latency_ms=latency_ms,
            error="No LLM providers are configured.",
        )

    error = "All configured LLM providers failed."
    if errors:
        error = f"{error} {'; '.join(errors)}"
    logger.error("FMS v2 LLM generation failed latency_ms=%s error=%s", latency_ms, error)
    return LlmResult(ok=False, latency_ms=latency_ms, error=error)


def _ordered_providers(
    providers: Sequence[LlmProviderSettings],
    provider_order: Sequence[LlmProviderName],
) -> list[LlmProviderSettings]:
    by_name = {provider.name: provider for provider in providers}
    return [by_name[name] for name in provider_order if name in by_name]


async def _call_provider(
    provider: LlmProviderSettings,
    request: GenerateFmsAnswerInput,
    *,
    caller: ChatCompletionCaller | None,
) -> str:
    if caller is not None:
        result = caller(provider, request)
        content = await result if inspect.isawaitable(result) else result
    else:
        content = await _call_openai_compatible_chat(provider, request)

    cleaned = str(content or "").strip()
    if not cleaned:
        raise ValueError("provider returned an empty answer")
    return cleaned


async def _call_openai_compatible_chat(
    provider: LlmProviderSettings,
    request: GenerateFmsAnswerInput,
) -> str:
    url = f"{provider.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": provider.model,
        "messages": [message.model_dump() for message in request.messages],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("provider response did not contain choices[0].message.content") from exc
