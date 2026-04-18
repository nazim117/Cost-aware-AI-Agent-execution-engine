# test_chat_prompt.py — seam test for the RAG → LLM prompt-construction path.
#
# The bug this test guards against: retrieved document chunks reach the RAG
# layer correctly but then disappear (or arrive with weak phrasing) in the
# messages list that is actually sent to DeepSeek.  That exact failure caused
# DeepSeek to claim "no access to your PM system" on 2026-04-18 even though
# sync and retrieval both worked.
#
# Approach: call the real /chat route with a real FastAPI test client but
# replace `llm.chat` with a spy that records its `messages` argument and
# returns a fixed string.  Qdrant / Ollama / SQLite are NOT required — we
# patch the three external I/O functions (embed, rag.retrieve, chat) with
# fakes that return deterministic values.
#
# What this tests:
#   1. When rag.retrieve returns a chunk, the prompt contains a
#      "--- PROJECT KNOWLEDGE ---" block.
#   2. The source label (e.g. "jira:KAN-1") appears in that block.
#   3. The system message uses directive language ("authoritative") and
#      not the old tentative phrasing ("may be relevant").
#   4. Prior assistant refusals injected via recent history are stripped.

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

# We import the app *after* all patches are in place in each test, so use
# a late import inside the fixtures instead of a module-level import.


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_PROJECT_ID = "proj-test-1234"
FAKE_SESSION_ID = "sess-test-5678"
FAKE_EMBED_VEC  = [0.0] * 768


def _make_chunk(source: str = "jira:KAN-1", text: str = "Fix login bug — Status: In Progress"):
    """Return a rag.Chunk-like object."""
    from rag import Chunk
    return Chunk(score=0.9, source=source, chunk_index=0, text=text)


# ---------------------------------------------------------------------------
# Helpers — the fake implementations injected via patch
# ---------------------------------------------------------------------------

async def _fake_embed(_text: str) -> list[float]:
    return FAKE_EMBED_VEC


async def _fake_retrieve(_project_id, _query, k, vstore):
    return [_make_chunk()]


async def _fake_retrieve_empty(_project_id, _query, k, vstore):
    return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_chunk_produces_project_knowledge_block():
    """When rag.retrieve returns a chunk, the LLM receives a PROJECT KNOWLEDGE block."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "Here is what I found: [jira:KAN-1]"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What tickets are in progress?",
            })

    assert resp.status_code == 200

    system_messages = [m for m in captured_messages if m["role"] == "system"]
    assert system_messages, "Expected at least one system message in the prompt"

    knowledge_block = next(
        (m for m in system_messages if "PROJECT KNOWLEDGE" in m["content"]),
        None,
    )
    assert knowledge_block is not None, (
        "Expected a '--- PROJECT KNOWLEDGE ---' block in the system messages.\n"
        f"System messages received: {[m['content'][:120] for m in system_messages]}"
    )
    assert "jira:KAN-1" in knowledge_block["content"]
    assert "authoritative" in knowledge_block["content"].lower()


@pytest.mark.asyncio
async def test_no_doc_chunks_no_knowledge_block():
    """When rag.retrieve returns nothing, no PROJECT KNOWLEDGE block is injected."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "I don't have that information."

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve_empty),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What tickets are in progress?",
            })

    assert resp.status_code == 200
    system_messages = [m for m in captured_messages if m["role"] == "system"]
    assert not any("PROJECT KNOWLEDGE" in m["content"] for m in system_messages)


@pytest.mark.asyncio
async def test_prior_refusals_stripped_from_recent_history():
    """Prior assistant refusals in recent history are filtered out of the prompt."""
    captured_messages = []

    refusal_turn = {"role": "assistant", "content": "I do not have access to your project management system."}
    good_turn    = {"role": "user",      "content": "What tickets are in progress?"}

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "KAN-1 is In Progress. [jira:KAN-1]"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[good_turn, refusal_turn]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "What tickets are in progress?",
            })

    assert resp.status_code == 200
    # The refusal turn must not appear in the prompt sent to DeepSeek.
    assert not any(
        "i do not have access" in m["content"].lower()
        for m in captured_messages
    ), "Refusal from previous turn leaked into the prompt"
    # The good user turn must still be present.
    assert any(m["content"] == good_turn["content"] for m in captured_messages)


@pytest.mark.asyncio
async def test_source_label_in_prompt():
    """The source label 'jira:KAN-1' appears verbatim in the knowledge block."""
    captured_messages = []

    async def _spy_chat(messages):
        captured_messages.extend(messages)
        return "KAN-1 is In Progress. [jira:KAN-1]"

    with (
        patch("main.embed", side_effect=_fake_embed),
        patch("main.rag.retrieve", side_effect=_fake_retrieve),
        patch("main.chat", side_effect=_spy_chat),
        patch("main.store.history", new_callable=AsyncMock, return_value=[]),
        patch("main.store.append", new_callable=AsyncMock),
        patch("main.vstore.search", new_callable=AsyncMock, return_value=[]),
        patch("main.vstore.upsert", new_callable=AsyncMock),
        patch("main._require_project", new_callable=AsyncMock),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/chat", json={
                "project_id": FAKE_PROJECT_ID,
                "session_id": FAKE_SESSION_ID,
                "message": "Who is working on KAN-1?",
            })

    all_content = " ".join(m["content"] for m in captured_messages)
    assert "jira:KAN-1" in all_content
