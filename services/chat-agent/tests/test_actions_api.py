# test_actions_api.py — HTTP-level tests for Step 7 action endpoints.
#
# Uses FastAPI's AsyncClient with monkeypatched singletons so no Qdrant,
# Ollama, DeepSeek, or mcp-server calls are made.
#
# Flow tested:
#   1. Propose a pending action via POST /projects/{id}/actions.
#   2. List via GET /projects/{id}/actions?status=pending — see it there.
#   3. Approve via POST /actions/{id}/approve — mcp.call dispatched correctly.
#   4. List pending again — now empty.
#   5. Reject path: propose → reject → list pending empty.

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from mcp_client import MCPError


FAKE_PROJECT_ID = "proj-api-test"
FAKE_SESSION_ID = "sess-api-test"

_GOOD_PAYLOAD = {
    "item_id": "ALPHA-99",
    "body": "Automated smoke-test comment",
    "ref_key": "jira_project_key",
}


class _FakeMCPClient:
    """Fake MCPClient for API-level action tests."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self._calls: list = []

    async def call(self, name: str, arguments: dict) -> dict:
        self._calls.append((name, arguments))
        idx = min(len(self._calls) - 1, len(self._responses) - 1)
        result = self._responses[idx]
        if isinstance(result, Exception):
            raise result
        return result


_COMMENT_RESULT = {
    "comment_id": "cmt-001",
    "url": "https://example.atlassian.net/browse/ALPHA-99?focusedCommentId=cmt-001",
    "created_at": "2026-01-01T10:00:00Z",
}

_FAKE_PROJECT_REFS = {"jira_project_key": "ALPHA"}


async def _fake_project_get(pid):
    from projects import Project
    return Project(id=pid, name="Test", created_at="2026", external_refs=_FAKE_PROJECT_REFS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_propose_returns_pending():
    """POST /projects/{id}/actions creates a pending action and returns it."""
    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._mcp", _FakeMCPClient([])),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:add_comment", "payload": _GOOD_PAYLOAD},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["action_type"] == "jira:add_comment"
    assert body["payload"]["item_id"] == "ALPHA-99"


@pytest.mark.asyncio
async def test_propose_invalid_action_type_returns_400():
    """Unknown action_type is rejected with 400."""
    with patch("main._require_project", new_callable=AsyncMock):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:delete_issue", "payload": _GOOD_PAYLOAD},
            )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_pending_shows_proposed_action():
    """GET /projects/{id}/actions?status=pending returns just-proposed action."""
    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._mcp", _FakeMCPClient([])),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:add_comment", "payload": _GOOD_PAYLOAD},
            )
            action_id = post_resp.json()["id"]

            list_resp = await client.get(
                f"/projects/{FAKE_PROJECT_ID}/actions", params={"status": "pending"}
            )

    assert list_resp.status_code == 200
    ids = [a["id"] for a in list_resp.json()]
    assert action_id in ids


@pytest.mark.asyncio
async def test_approve_calls_mcp_and_returns_executed():
    """POST /actions/{id}/approve dispatches to mcp.call and returns status=executed."""
    fake_mcp = _FakeMCPClient([_COMMENT_RESULT])

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._mcp", fake_mcp),
        patch("main.project_store.get", side_effect=_fake_project_get),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:add_comment", "payload": _GOOD_PAYLOAD},
            )
            action_id = post_resp.json()["id"]
            approve_resp = await client.post(f"/actions/{action_id}/approve")

    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "executed"
    assert body["result"]["id"] == "cmt-001"
    assert fake_mcp._calls[0] == ("jira_add_comment", {"key": "ALPHA-99", "body": "Automated smoke-test comment"})


@pytest.mark.asyncio
async def test_approve_removes_from_pending_list():
    """After approval, the action is no longer in the pending list."""
    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._mcp", _FakeMCPClient([_COMMENT_RESULT])),
        patch("main.project_store.get", side_effect=_fake_project_get),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:add_comment", "payload": _GOOD_PAYLOAD},
            )
            action_id = post_resp.json()["id"]

            await client.post(f"/actions/{action_id}/approve")

            list_resp = await client.get(
                f"/projects/{FAKE_PROJECT_ID}/actions", params={"status": "pending"}
            )

    pending_ids = [a["id"] for a in list_resp.json()]
    assert action_id not in pending_ids


@pytest.mark.asyncio
async def test_reject_returns_rejected_and_empties_pending():
    """POST /actions/{id}/reject marks rejected; action disappears from pending list."""
    fake_mcp = _FakeMCPClient([])

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._mcp", fake_mcp),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:add_comment", "payload": _GOOD_PAYLOAD},
            )
            action_id = post_resp.json()["id"]

            reject_resp = await client.post(f"/actions/{action_id}/reject")

            list_resp = await client.get(
                f"/projects/{FAKE_PROJECT_ID}/actions", params={"status": "pending"}
            )

    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"
    # mcp.call was never invoked (no approve happened).
    assert fake_mcp._calls == []
    pending_ids = [a["id"] for a in list_resp.json()]
    assert action_id not in pending_ids


@pytest.mark.asyncio
async def test_approve_mcp_failure_returns_502():
    """When mcp.call raises, approve returns 502 and action is marked failed."""
    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._mcp", _FakeMCPClient([MCPError("401 Unauthorized")])),
        patch("main.project_store.get", side_effect=_fake_project_get),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            post_resp = await client.post(
                f"/projects/{FAKE_PROJECT_ID}/actions",
                json={"action_type": "jira:add_comment", "payload": _GOOD_PAYLOAD},
            )
            action_id = post_resp.json()["id"]
            approve_resp = await client.post(f"/actions/{action_id}/approve")

    assert approve_resp.status_code == 502
    assert "401 Unauthorized" in approve_resp.json()["detail"]
