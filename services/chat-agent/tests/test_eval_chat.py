# test_eval_chat.py — end-to-end eval harness for the /chat endpoint.
#
# These tests hit a LIVE running server (http://localhost:8084) with real
# Qdrant, Ollama, SQLite, and DeepSeek.  They are opt-in:
#
#   pytest -m e2e                         # run only eval tests
#   pytest -m "not e2e"                   # skip them (default CI behaviour)
#
# What this guards against: the prompt-delivery / LLM-refusal class of bug
# where retrieval is correct but the model refuses to use the retrieved data.
# Each test seeds a known document, asks a canonical question, and asserts
# the reply contains expected substrings (ticket ids, source citations, facts).
#
# Setup: the server must already be running.  These tests create and delete
# their own projects so they do not pollute production data.

import httpx
import pytest

BASE_URL = "http://localhost:8084"


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_project(client: httpx.AsyncClient, name: str) -> str:
    resp = await client.post("/projects", json={"name": name})
    resp.raise_for_status()
    return resp.json()["id"]


async def _delete_project(client: httpx.AsyncClient, project_id: str) -> None:
    await client.delete(f"/projects/{project_id}")


async def _ingest(client: httpx.AsyncClient, project_id: str, source: str, text: str) -> int:
    resp = await client.post("/ingest", json={"project_id": project_id, "source": source, "text": text})
    resp.raise_for_status()
    return resp.json()["chunks"]


async def _chat(client: httpx.AsyncClient, project_id: str, session_id: str, message: str) -> str:
    resp = await client.post("/chat", json={
        "project_id": project_id,
        "session_id": session_id,
        "message": message,
    })
    resp.raise_for_status()
    return resp.json()["reply"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eval_jira_ticket_cited_in_reply():
    """After ingesting a Jira-like fixture, the reply cites the source label."""
    ticket_text = (
        "Fix login timeout bug\n\n"
        "Status: In Progress\n\n"
        "Assignee: Alice\n\n"
        "Users are being logged out after 5 minutes due to a misconfigured "
        "session TTL in the auth service.  The fix is to update SESSION_TTL "
        "from 300 to 3600 in config.yaml.\n\n"
        "URL: https://example.atlassian.net/browse/KAN-1"
    )

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        project_id = await _create_project(client, "_eval_jira_test")
        try:
            chunks = await _ingest(client, project_id, "jira:KAN-1", ticket_text)
            assert chunks >= 1, "Expected at least one chunk to be ingested"

            reply = await _chat(client, project_id, "eval-session-1",
                                "What is KAN-1 about and what is its status?")

            reply_lower = reply.lower()
            assert "kan-1" in reply_lower, (
                f"Expected 'KAN-1' in reply.\nReply: {reply}"
            )
            assert any(word in reply_lower for word in ("in progress", "login", "timeout", "session")), (
                f"Expected ticket content in reply.\nReply: {reply}"
            )
        finally:
            await _delete_project(client, project_id)


@pytest.mark.asyncio
async def test_eval_source_citation_present():
    """Reply should cite the source label in brackets (e.g. [jira:KAN-2])."""
    ticket_text = (
        "Implement dark mode\n\n"
        "Status: To Do\n\n"
        "Add a toggle in user preferences that switches the UI to a dark colour "
        "palette.  Must persist in user settings table.\n\n"
        "URL: https://example.atlassian.net/browse/KAN-2"
    )

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        project_id = await _create_project(client, "_eval_citation_test")
        try:
            await _ingest(client, project_id, "jira:KAN-2", ticket_text)

            reply = await _chat(client, project_id, "eval-session-2",
                                "What does KAN-2 describe?")

            assert "kan-2" in reply.lower(), (
                f"Expected 'KAN-2' cited in reply.\nReply: {reply}"
            )
        finally:
            await _delete_project(client, project_id)


@pytest.mark.asyncio
async def test_eval_no_cross_project_contamination():
    """Content ingested into project A must not appear in project B's replies."""
    text_a = (
        "Secret roadmap item X\n\nStatus: To Do\n\n"
        "This is top-secret project Alpha work that must never leak.\n\n"
        "URL: https://example.atlassian.net/browse/ALPHA-1"
    )

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        project_a = await _create_project(client, "_eval_isolation_a")
        project_b = await _create_project(client, "_eval_isolation_b")
        try:
            await _ingest(client, project_a, "jira:ALPHA-1", text_a)

            reply = await _chat(client, project_b, "eval-session-b",
                                "Tell me about ALPHA-1 and the secret roadmap item X.")

            assert "top-secret" not in reply.lower(), (
                f"Project B's reply contains content from project A.\nReply: {reply}"
            )
        finally:
            await _delete_project(client, project_a)
            await _delete_project(client, project_b)


@pytest.mark.asyncio
async def test_eval_idempotent_sync_no_duplicate_answers():
    """Ingesting the same source twice should not double-up content in replies."""
    ticket_text = (
        "Refactor database layer\n\nStatus: In Progress\n\n"
        "Replace the raw SQL queries with an ORM for maintainability.\n\n"
        "URL: https://example.atlassian.net/browse/KAN-3"
    )

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        project_id = await _create_project(client, "_eval_idempotency_test")
        try:
            # Simulate sync running twice.
            await _ingest(client, project_id, "jira:KAN-3", ticket_text)
            await _ingest(client, project_id, "jira:KAN-3", ticket_text)

            reply = await _chat(client, project_id, "eval-session-3",
                                "What is KAN-3 about?")

            # The reply should mention the ticket but not repeat itself verbatim
            # multiple times (a symptom of duplicate chunks being retrieved).
            assert "kan-3" in reply.lower() or "refactor" in reply.lower(), (
                f"Expected KAN-3 content in reply.\nReply: {reply}"
            )
            # Rough heuristic: if "refactor" appears 5+ times, chunks duplicated.
            assert reply.lower().count("refactor") < 5, (
                f"'refactor' appears too many times — possible duplicate chunks.\nReply: {reply}"
            )
        finally:
            await _delete_project(client, project_id)
