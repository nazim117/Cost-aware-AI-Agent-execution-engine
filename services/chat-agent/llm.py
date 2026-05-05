# LLM client — provider-agnostic chat completion.
#
# Two backends are supported:
#   ollama            — local Ollama HTTP server (default; zero cloud egress)
#   openai_compatible — any OpenAI-compatible /chat/completions endpoint
#                       (GitHub Models, DeepSeek, OpenAI, etc.) configured via
#                       OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL env vars
#
# Provider is fixed at deploy time via LLM_PROVIDER.  End users cannot override
# which model sees their data — that decision belongs to the admin who deploys
# the service.

import httpx
from fastapi import HTTPException

from config import settings


# ─── Backend implementations ──────────────────────────────────────────────────

async def _chat_ollama(messages: list[dict]) -> str:
    # Ollama's native /api/chat endpoint.  stream:false makes it return a single
    # JSON response instead of a newline-delimited stream.
    # Response shape differs from OpenAI: top-level "message", not "choices".
    payload = {
        "model": settings.ollama_chat_model,
        "messages": messages,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(
            502, f"Cannot reach Ollama at {settings.ollama_base_url}: {exc}"
        ) from exc
    if r.status_code != 200:
        raise HTTPException(502, f"Ollama returned {r.status_code}: {r.text}")
    return r.json()["message"]["content"]


async def _chat_openai_compatible(messages: list[dict]) -> str:
    # Shared client for any OpenAI-compatible /chat/completions endpoint.
    # GitHub Models, DeepSeek, and vanilla OpenAI all use this shape.
    payload = {
        "model": settings.openai_model,
        "messages": messages,
        "max_tokens": 1024,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    label = settings.openai_provider_label
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{settings.openai_base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Cannot reach {label}: {exc}") from exc
    if r.status_code != 200:
        raise HTTPException(502, f"{label} returned {r.status_code}: {r.text}")
    return r.json()["choices"][0]["message"]["content"]


# ─── Dispatcher ───────────────────────────────────────────────────────────────

async def chat(messages: list[dict]) -> str:
    """Route a chat completion to the backend fixed in LLM_PROVIDER.

    Args:
        messages: Full conversation history as {"role", "content"} dicts.
                  The entire history must be sent — LLMs are stateless.

    Returns:
        The assistant reply as a plain string.

    Raises:
        HTTPException(500): Unknown LLM_PROVIDER value (config bug).
        HTTPException(502): Backend unreachable or returned an error.
    """
    p = settings.llm_provider
    if p == "ollama":
        return await _chat_ollama(messages)
    elif p == "openai_compatible":
        return await _chat_openai_compatible(messages)
    else:
        raise HTTPException(
            500,
            f"Unknown LLM_PROVIDER {p!r}. Valid options: ollama, openai_compatible. "
            "Fix the LLM_PROVIDER env var and restart.",
        )


# ─── Startup validation ────────────────────────────────────────────────────────

def validate_llm_config() -> None:
    """Fail fast at startup if the LLM config is incomplete.

    Called from the FastAPI lifespan hook before the server begins accepting
    requests.  Raises RuntimeError with a clear message so the admin knows
    exactly which env var is missing.
    """
    p = settings.llm_provider
    if p == "ollama":
        if not settings.ollama_chat_model:
            raise RuntimeError(
                "OLLAMA_CHAT_MODEL must not be empty when LLM_PROVIDER=ollama"
            )
    elif p == "openai_compatible":
        for field in ("openai_base_url", "openai_api_key", "openai_model"):
            if not getattr(settings, field):
                raise RuntimeError(
                    f"{field.upper()} must be set when LLM_PROVIDER=openai_compatible"
                )
    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER {p!r}. Valid options: ollama, openai_compatible"
        )
