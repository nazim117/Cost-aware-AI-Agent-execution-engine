"""Unit tests for llm.py — deploy-time-only LLM dispatcher.

Provider and model are fixed by env vars (LLM_PROVIDER, OPENAI_*, OLLAMA_*).
End-user requests cannot override them.  All HTTP calls are intercepted with
unittest.mock — no real network needed.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import llm as llm_module
from llm import chat, validate_llm_config

MESSAGES = [{"role": "user", "content": "hello"}]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mock_response(status: int, body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


def _ollama_ok(content: str) -> MagicMock:
    return _mock_response(200, {"message": {"role": "assistant", "content": content}})


def _openai_ok(content: str) -> MagicMock:
    return _mock_response(
        200, {"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


# ─── Ollama backend ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ollama_returns_content():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(return_value=_ollama_ok("hi from ollama"))
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.llm_provider = "ollama"
            s.ollama_base_url = "http://localhost:11434"
            s.ollama_chat_model = "llama3"
            result = await chat(MESSAGES)
    assert result == "hi from ollama"


@pytest.mark.asyncio
async def test_ollama_502_on_non_200():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(
        return_value=_mock_response(500, {"error": "internal"})
    )
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.ollama_base_url = "http://localhost:11434"
            s.ollama_chat_model = "llama3"
            with pytest.raises(HTTPException) as exc:
                await llm_module._chat_ollama(MESSAGES)
    assert exc.value.status_code == 502


# ─── OpenAI-compatible backend ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_compatible_returns_content():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(
        return_value=_openai_ok("hi from openai-compatible")
    )
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.openai_base_url = "https://models.github.ai/inference"
            s.openai_api_key = "ghp_test"
            s.openai_model = "openai/gpt-4o-mini"
            s.openai_provider_label = "GitHub Models"
            result = await llm_module._chat_openai_compatible(MESSAGES)
    assert result == "hi from openai-compatible"


@pytest.mark.asyncio
async def test_openai_compatible_502_on_non_200():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.post = AsyncMock(
        return_value=_mock_response(401, {"error": "unauthorized"})
    )
    with patch("llm.httpx.AsyncClient", return_value=mock_client):
        with patch("llm.settings") as s:
            s.openai_base_url = "https://models.github.ai/inference"
            s.openai_api_key = "bad-key"
            s.openai_model = "openai/gpt-4o-mini"
            s.openai_provider_label = "GitHub Models"
            with pytest.raises(HTTPException) as exc:
                await llm_module._chat_openai_compatible(MESSAGES)
    assert exc.value.status_code == 502


# ─── Dispatcher ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatcher_routes_ollama():
    with patch("llm._chat_ollama", new_callable=AsyncMock, return_value="ollama-reply") as mock_fn:
        with patch("llm.settings") as s:
            s.llm_provider = "ollama"
            result = await chat(MESSAGES)
    mock_fn.assert_called_once_with(MESSAGES)
    assert result == "ollama-reply"


@pytest.mark.asyncio
async def test_dispatcher_routes_openai_compatible():
    with patch("llm._chat_openai_compatible", new_callable=AsyncMock, return_value="cloud-reply") as mock_fn:
        with patch("llm.settings") as s:
            s.llm_provider = "openai_compatible"
            result = await chat(MESSAGES)
    mock_fn.assert_called_once_with(MESSAGES)
    assert result == "cloud-reply"


@pytest.mark.asyncio
async def test_dispatcher_unknown_provider_raises_500():
    with patch("llm.settings") as s:
        s.llm_provider = "unknown-provider"
        with pytest.raises(HTTPException) as exc:
            await chat(MESSAGES)
    assert exc.value.status_code == 500
    assert "unknown-provider" in exc.value.detail


# ─── Startup validation ────────────────────────────────────────────────────────

def test_validate_ollama_ok():
    with patch("llm.settings") as s:
        s.llm_provider = "ollama"
        s.ollama_chat_model = "llama3"
        validate_llm_config()  # must not raise


def test_validate_ollama_empty_model_raises():
    with patch("llm.settings") as s:
        s.llm_provider = "ollama"
        s.ollama_chat_model = ""
        with pytest.raises(RuntimeError, match="OLLAMA_CHAT_MODEL"):
            validate_llm_config()


def test_validate_openai_compatible_ok():
    with patch("llm.settings") as s:
        s.llm_provider = "openai_compatible"
        s.openai_base_url = "https://models.github.ai/inference"
        s.openai_api_key = "ghp_test"
        s.openai_model = "openai/gpt-4o-mini"
        validate_llm_config()  # must not raise


@pytest.mark.parametrize("missing_field", [
    "openai_base_url",
    "openai_api_key",
    "openai_model",
])
def test_validate_openai_missing_field_raises(missing_field):
    with patch("llm.settings") as s:
        s.llm_provider = "openai_compatible"
        s.openai_base_url = "https://models.github.ai/inference"
        s.openai_api_key = "ghp_test"
        s.openai_model = "openai/gpt-4o-mini"
        setattr(s, missing_field, "")
        with pytest.raises(RuntimeError, match=missing_field.upper()):
            validate_llm_config()


def test_validate_unknown_provider_raises():
    with patch("llm.settings") as s:
        s.llm_provider = "bad-provider"
        with pytest.raises(RuntimeError, match="bad-provider"):
            validate_llm_config()
