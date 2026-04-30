# DeepSeek chat completions client.
#
# DeepSeek's API is OpenAI-compatible: the request and response JSON shapes are
# identical to OpenAI's /v1/chat/completions endpoint.  That means we can use any
# HTTP client — no vendor SDK required.  We use httpx because it is async and
# already in our requirements.
#
# The one function exported here, `chat()`, takes a list of messages (the full
# conversation history) and returns the model's reply as a plain string.

import httpx
from fastapi import HTTPException

from config import settings


async def chat(messages: list[dict]) -> str:
    """Send a conversation to DeepSeek and return the assistant's reply.

    Args:
        messages: List of {"role": "user"|"assistant"|"system", "content": str}.
                  The entire history must be included — the model is stateless.

    Returns:
        The text content of the model's reply.

    Raises:
        HTTPException(502): If DeepSeek is unreachable or returns an error.
    """
    payload = {
        "model": settings.deepseek_model,
        "messages": messages,
        # max_tokens caps the reply length.  1024 is generous for most chat turns.
        "max_tokens": 1024,
    }

    headers = {
        # Bearer token auth — the API key proves we are an authorised caller.
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    try:
        # Use an async context manager so the connection is closed when done.
        # timeout=30 seconds covers slow model responses without hanging forever.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.deepseek_base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as exc:
        # Network-level failure: DNS lookup failed, connection refused, etc.
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach DeepSeek API: {exc}",
        ) from exc

    if response.status_code != 200:
        # DeepSeek returned an error (e.g. 401 invalid key, 429 rate limit).
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek returned {response.status_code}: {response.text}",
        )

    data = response.json()

    # The OpenAI-compatible response shape:
    # {
    #   "choices": [
    #     { "message": { "role": "assistant", "content": "..." } }
    #   ]
    # }
    return data["choices"][0]["message"]["content"]
