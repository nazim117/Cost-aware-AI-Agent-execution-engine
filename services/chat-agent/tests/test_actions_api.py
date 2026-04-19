# test_actions_api.py — HTTP-level tests for Step 7 action endpoints.
#
# Uses FastAPI's TestClient (synchronous ASGI wrapper) with monkeypatched
# singletons so no Qdrant, Ollama, DeepSeek, or Jira/GitHub calls are made.
#
# Flow tested:
#   1. Propose a pending action via POST /projects/{id}/actions.
#   2. List via GET /projects/{id}/actions?status=pending — see it there.
#   3. Approve via POST /actions/{id}/approve — integration add_comment called.
#   4. List pending again — now empty.
#   5. Reject path: propose → reject → list pending empty.

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport


FAKE_PROJECT_ID = "proj-api-test"
FAKE_SESSION_ID = "sess-api-test"

_GOOD_PAYLOAD = {
    "item_id": "ALPHA-99",
    "body": "Automated smoke-test comment",
    "ref_key": "jira_project_key",
}


class _FakeIntegration:
    """add_comment returns a fixed dict without hitting the network."""
    def __init__(self):
        self.add_comment = AsyncMock(return_value={
            "id": "cmt-001",
            "url": "https://example.atlassian.net/browse/ALPHA-99?focusedCommentId=cmt-001",
            "created_at": "2026-01-01T10:00:00Z",
        })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_propose_returns_pending():
    """POST /projects/{id}/actions creates a pending action and returns it."""
    fake_integration = _FakeIntegration()

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._integrations", {"jira_project_key": fake_integration}),
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
    fake_integration = _FakeIntegration()

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._integrations", {"jira_project_key": fake_integration}),
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
async def test_approve_calls_integration_and_returns_executed():
    """POST /actions/{id}/approve executes the action and returns status=executed."""
    fake_integration = _FakeIntegration()

    async def _fake_project_get(pid):
        from projects import Project
        return Project(
            id=pid, name="Test", created_at="2026",
            external_refs={"jira_project_key": "ALPHA"},
        )

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._integrations", {"jira_project_key": fake_integration}),
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
    fake_integration.add_comment.assert_called_once()


@pytest.mark.asyncio
async def test_approve_removes_from_pending_list():
    """After approval, the action is no longer in the pending list."""
    fake_integration = _FakeIntegration()

    async def _fake_project_get(pid):
        from projects import Project
        return Project(
            id=pid, name="Test", created_at="2026",
            external_refs={"jira_project_key": "ALPHA"},
        )

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._integrations", {"jira_project_key": fake_integration}),
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
    fake_integration = _FakeIntegration()

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._integrations", {"jira_project_key": fake_integration}),
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
    # Integration was never called.
    fake_integration.add_comment.assert_not_called()
    pending_ids = [a["id"] for a in list_resp.json()]
    assert action_id not in pending_ids


@pytest.mark.asyncio
async def test_approve_integration_failure_returns_502():
    """When the integration raises, approve returns 502 and action is marked failed."""
    class _FailingIntegration:
        add_comment = AsyncMock(side_effect=Exception("401 Unauthorized"))

    failing = _FailingIntegration()

    async def _fake_project_get(pid):
        from projects import Project
        return Project(
            id=pid, name="Test", created_at="2026",
            external_refs={"jira_project_key": "ALPHA"},
        )

    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main._integrations", {"jira_project_key": failing}),
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
