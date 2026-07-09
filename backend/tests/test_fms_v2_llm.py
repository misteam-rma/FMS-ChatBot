"""Unit tests for the FMS v2 multi-provider LLM fallback client."""

import pytest
from pydantic import ValidationError

from app.fms_v2.config import DEFAULT_LLM_PROVIDER_ORDER, LlmProviderSettings
from app.fms_v2.llm import GenerateFmsAnswerInput, generate_fms_answer


def provider(name, *, api_key="test-key", model="test-model", timeout_seconds=1):
    return LlmProviderSettings(
        name=name,
        api_key=api_key,
        model=model,
        base_url=f"https://{name}.example/v1",
        timeout_seconds=timeout_seconds,
    )


def request(provider_order=None):
    data = {
        "messages": [
            {"role": "system", "content": "Answer only from supplied FMS records."},
            {"role": "user", "content": "What is the current status?"},
        ],
        "max_tokens": 200,
    }
    if provider_order is not None:
        data["provider_order"] = provider_order
    return data


def test_default_provider_order_is_required_fallback_order():
    req = GenerateFmsAnswerInput.model_validate(
        {"messages": [{"role": "user", "content": "hi"}]}
    )

    assert DEFAULT_LLM_PROVIDER_ORDER == ("cerebras", "groq", "nvidia", "openai")
    assert req.provider_order == ["cerebras", "groq", "nvidia", "openai"]


@pytest.mark.asyncio
async def test_llm_fallback_tries_next_provider_after_failure():
    calls = []

    async def fake_caller(provider_config, _request):
        calls.append(provider_config.name)
        if provider_config.name == "cerebras":
            raise RuntimeError("temporary upstream failure")
        return "Status pending. Source: FMS1 row 7, Status column."

    result = await generate_fms_answer(
        request(),
        providers=[
            provider("cerebras", model="gpt-oss-120b"),
            provider("groq", model="gpt-oss-120b"),
            provider("openai", model="gpt-4o-mini"),
        ],
        caller=fake_caller,
    )

    assert result.ok is True
    assert result.provider == "groq"
    assert result.model == "gpt-oss-120b"
    assert result.content.startswith("Status pending")
    assert calls == ["cerebras", "groq"]


@pytest.mark.asyncio
async def test_llm_fallback_skips_unconfigured_providers():
    calls = []

    def fake_caller(provider_config, _request):
        calls.append(provider_config.name)
        return "Answered from source columns."

    result = await generate_fms_answer(
        request(provider_order=["cerebras", "nvidia", "openai"]),
        providers=[
            provider("cerebras", api_key="", model="gpt-oss-120b"),
            provider("nvidia", api_key="test-key", model=""),
            provider("openai", model="gpt-4o-mini"),
        ],
        caller=fake_caller,
    )

    assert result.ok is True
    assert result.provider == "openai"
    assert calls == ["openai"]


@pytest.mark.asyncio
async def test_llm_fallback_reports_no_configured_providers():
    result = await generate_fms_answer(
        request(provider_order=["cerebras", "nvidia"]),
        providers=[
            provider("cerebras", api_key="", model="gpt-oss-120b"),
            provider("nvidia", api_key="test-key", model=""),
        ],
        caller=lambda *_args: "should not be called",
    )

    assert result.ok is False
    assert result.error == "No LLM providers are configured."


@pytest.mark.asyncio
async def test_llm_fallback_returns_structured_error_when_all_fail():
    async def fake_caller(provider_config, _request):
        raise RuntimeError(f"{provider_config.name} failed")

    result = await generate_fms_answer(
        request(provider_order=["cerebras", "groq"]),
        providers=[
            provider("cerebras", model="gpt-oss-120b"),
            provider("groq", model="gpt-oss-120b"),
        ],
        caller=fake_caller,
    )

    assert result.ok is False
    assert result.content == ""
    assert "All configured LLM providers failed" in (result.error or "")
    assert "cerebras failed" in (result.error or "")
    assert "groq failed" in (result.error or "")


def test_llm_request_rejects_duplicate_provider_order():
    with pytest.raises(ValidationError):
        GenerateFmsAnswerInput.model_validate(
            {
                "messages": [{"role": "user", "content": "Hello"}],
                "provider_order": ["groq", "groq"],
            }
        )
