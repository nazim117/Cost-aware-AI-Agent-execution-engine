# test_integrations.py — unit tests for the Jira and GitHub adapters and SyncStore.
#
# No live network calls.  HTTP responses are faked via a custom AsyncBaseTransport
# (httpx's official extension point for testing).  No external test libraries needed.
#
# SyncStore tests use a fresh SQLite temp file per test (tmp_path fixture).

import pytest
import httpx

from integrations.jira import JiraIntegration
from integrations.github import GitHubIntegration
from sync import SyncStore


# ---------------------------------------------------------------------------
# Helpers — mock HTTP transport
# ---------------------------------------------------------------------------

class _MockTransport(httpx.AsyncBaseTransport):
    """Return a pre-built list of httpx.Response objects in order.

    Each call to handle_async_request pops the next response.  If the list
    is exhausted, the last response is returned again (handles pagination
    loops that may make one extra check call).
    """

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._responses[min(self._index, len(self._responses) - 1)]
        self._index += 1
        # httpx requires the response to be associated with a request.
        resp._request = request  # type: ignore[attr-defined]
        return resp

    @property
    def last_request(self) -> httpx.Request | None:
        return self._responses[min(self._index - 1, len(self._responses) - 1)]._request  # type: ignore[attr-defined]


def _jira_issue(key: str, summary: str, desc: str = "", status: str = "To Do") -> dict:
    """Build a minimal Jira issue dict matching the v3 /search response shape."""
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": desc,
            "status": {"name": status},
            "assignee": None,
            "updated": "2024-06-01T10:00:00.000+0000",
        },
    }


def _github_issue(number: int, title: str, body: str = "", state: str = "open") -> dict:
    """Build a minimal GitHub issue dict matching the REST v3 shape."""
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": state,
        "assignee": None,
        "html_url": f"https://github.com/org/repo/issues/{number}",
        "updated_at": "2024-06-01T10:00:00Z",
    }


# ---------------------------------------------------------------------------
# JiraIntegration tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("issues,expected_count", [
    ([_jira_issue("ALPHA-1", "First issue")], 1),
    ([_jira_issue("ALPHA-1", "A"), _jira_issue("ALPHA-2", "B")], 2),
    ([], 0),
])
async def test_jira_fetch_items_count(issues, expected_count):
    """fetch_items returns one Item per Jira issue."""
    payload = {"issues": issues, "total": len(issues)}
    transport = _MockTransport([httpx.Response(200, json=payload)])
    jira = JiraIntegration("https://example.atlassian.net", "u@e.com", "tok", transport=transport)

    items = await jira.fetch_items({"jira_project_key": "ALPHA"})

    assert len(items) == expected_count


async def test_jira_fetch_items_fields():
    """Item fields are mapped correctly from the Jira response."""
    issue = _jira_issue("ALPHA-42", "Fix the bug", "Some description", "In Progress")
    payload = {"issues": [issue], "total": 1}
    transport = _MockTransport([httpx.Response(200, json=payload)])
    jira = JiraIntegration("https://example.atlassian.net", "u@e.com", "tok", transport=transport)

    items = await jira.fetch_items({"jira_project_key": "ALPHA"})

    assert items[0].id == "ALPHA-42"
    assert items[0].title == "Fix the bug"
    assert items[0].body == "Some description"
    assert items[0].status == "In Progress"
    assert items[0].url == "https://example.atlassian.net/browse/ALPHA-42"


async def test_jira_fetch_items_adf_description():
    """ADF description objects are converted to plain text."""
    adf = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": " world"}],
            }
        ],
    }
    issue = _jira_issue("ALPHA-1", "ADF issue", desc=adf)
    payload = {"issues": [issue], "total": 1}
    transport = _MockTransport([httpx.Response(200, json=payload)])
    jira = JiraIntegration("https://example.atlassian.net", "u@e.com", "tok", transport=transport)

    items = await jira.fetch_items({"jira_project_key": "ALPHA"})

    assert "Hello" in items[0].body
    assert "world" in items[0].body


async def test_jira_fetch_items_empty_ref():
    """fetch_items returns [] immediately if jira_project_key is absent."""
    transport = _MockTransport([httpx.Response(200, json={})])
    jira = JiraIntegration("https://example.atlassian.net", "u@e.com", "tok", transport=transport)

    items = await jira.fetch_items({})  # no jira_project_key

    assert items == []


async def test_jira_fetch_items_updated_since_in_jql():
    """updated_since is translated into a JQL date filter in the POST body."""
    payload = {"issues": []}
    captured: list[httpx.Request] = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=payload)

    jira = JiraIntegration(
        "https://example.atlassian.net", "u@e.com", "tok",
        transport=CapturingTransport(),
    )
    await jira.fetch_items(
        {"jira_project_key": "ALPHA"},
        updated_since="2024-03-15T08:00:00+00:00",
    )

    assert captured
    import json as _json
    body = _json.loads(captured[0].content)
    assert "updated >=" in body["jql"]
    assert "2024-03-15" in body["jql"]


# ---------------------------------------------------------------------------
# GitHubIntegration tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("issues,expected_count", [
    ([_github_issue(1, "First")], 1),
    ([_github_issue(1, "A"), _github_issue(2, "B")], 2),
    ([], 0),
])
async def test_github_fetch_items_count(issues, expected_count):
    """fetch_items returns one Item per GitHub issue."""
    transport = _MockTransport([httpx.Response(200, json=issues)])
    gh = GitHubIntegration("token123", transport=transport)

    items = await gh.fetch_items({"github_repo": "org/repo"})

    assert len(items) == expected_count


async def test_github_fetch_items_fields():
    """Item fields are mapped correctly from the GitHub response."""
    issue = _github_issue(17, "Add feature", "Feature description", "closed")
    transport = _MockTransport([httpx.Response(200, json=[issue])])
    gh = GitHubIntegration("token123", transport=transport)

    items = await gh.fetch_items({"github_repo": "org/repo"})

    assert items[0].id == "17"
    assert items[0].title == "Add feature"
    assert items[0].body == "Feature description"
    assert items[0].status == "closed"
    assert items[0].url == "https://github.com/org/repo/issues/17"


async def test_github_fetch_items_empty_ref():
    """fetch_items returns [] immediately if github_repo is absent."""
    gh = GitHubIntegration("token123")
    items = await gh.fetch_items({})
    assert items == []


async def test_github_fetch_items_since_param():
    """updated_since is passed as the `since` query param."""
    captured: list[httpx.Request] = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(200, json=[])

    gh = GitHubIntegration("token123", transport=CapturingTransport())
    await gh.fetch_items(
        {"github_repo": "org/repo"},
        updated_since="2024-06-01T00:00:00+00:00",
    )

    assert captured
    assert captured[0].url.params.get("since") == "2024-06-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# SyncStore tests
# ---------------------------------------------------------------------------

async def test_sync_store_last_synced_none_initially(tmp_path):
    """A fresh store returns None for last_synced_at."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    result = await store.get_last_synced("proj-1", "jira_project_key", "ALPHA")
    assert result is None


async def test_sync_store_set_and_get(tmp_path):
    """set_last_synced / get_last_synced round-trip."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    ts = "2024-06-01T12:00:00+00:00"
    await store.set_last_synced("proj-1", "jira_project_key", "ALPHA", ts)
    assert await store.get_last_synced("proj-1", "jira_project_key", "ALPHA") == ts


async def test_sync_store_set_overwrites(tmp_path):
    """Calling set_last_synced twice updates the timestamp."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    await store.set_last_synced("proj-1", "github_repo", "org/repo", "2024-01-01T00:00:00Z")
    ts2 = "2024-06-01T00:00:00Z"
    await store.set_last_synced("proj-1", "github_repo", "org/repo", ts2)
    assert await store.get_last_synced("proj-1", "github_repo", "org/repo") == ts2


async def test_sync_store_record_action_does_not_raise(tmp_path):
    """record_action writes a row without error."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    await store.record_action("proj-1", "sync", {"items": 5, "chunks": 10})


async def test_sync_store_get_sync_status_empty(tmp_path):
    """get_sync_status returns [] for a project that has never been synced."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    assert await store.get_sync_status("proj-1") == []


async def test_sync_store_delete_by_project(tmp_path):
    """delete_by_project removes only the target project's rows."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    await store.set_last_synced("proj-1", "github_repo", "org/repo", "2024-01-01T00:00:00Z")
    await store.set_last_synced("proj-2", "github_repo", "org/repo", "2024-01-01T00:00:00Z")
    await store.delete_by_project("proj-1")
    assert await store.get_last_synced("proj-1", "github_repo", "org/repo") is None
    assert await store.get_last_synced("proj-2", "github_repo", "org/repo") is not None


# ---------------------------------------------------------------------------
# JiraIntegration.add_comment tests
# ---------------------------------------------------------------------------

async def test_jira_add_comment_returns_id_and_url():
    """add_comment POSTs to the correct URL and returns id + deeplink URL."""
    comment_response = {
        "id": "12345",
        "created": "2026-01-01T10:00:00.000+0000",
    }
    captured: list[httpx.Request] = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(201, json=comment_response)

    jira = JiraIntegration(
        "https://example.atlassian.net", "u@e.com", "tok",
        transport=CapturingTransport(),
    )

    result = await jira.add_comment(
        {"jira_project_key": "ALPHA"}, "ALPHA-12", "Smoke test passed"
    )

    assert result["id"] == "12345"
    assert "ALPHA-12" in result["url"]
    assert "12345" in result["url"]   # focusedCommentId in URL
    assert captured
    assert captured[0].method == "POST"
    assert "/rest/api/3/issue/ALPHA-12/comment" in str(captured[0].url)


async def test_jira_add_comment_body_is_adf():
    """The request body must be valid ADF (not a plain string)."""
    captured: list[httpx.Request] = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(201, json={"id": "1", "created": ""})

    jira = JiraIntegration(
        "https://example.atlassian.net", "u@e.com", "tok",
        transport=CapturingTransport(),
    )
    await jira.add_comment({"jira_project_key": "ALPHA"}, "ALPHA-1", "hello world")

    import json as _json
    body = _json.loads(captured[0].content)
    adf = body["body"]
    assert adf["version"] == 1
    assert adf["type"] == "doc"
    assert adf["content"][0]["type"] == "paragraph"
    assert adf["content"][0]["content"][0]["type"] == "text"
    assert "hello world" in adf["content"][0]["content"][0]["text"]


# ---------------------------------------------------------------------------
# GitHubIntegration.add_comment tests
# ---------------------------------------------------------------------------

async def test_github_add_comment_returns_id_and_url():
    """add_comment POSTs to the correct URL and returns id + html_url."""
    comment_response = {
        "id": 9876,
        "html_url": "https://github.com/org/repo/issues/42#issuecomment-9876",
        "created_at": "2026-01-01T10:00:00Z",
    }
    captured: list[httpx.Request] = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(201, json=comment_response)

    gh = GitHubIntegration("token123", transport=CapturingTransport())

    result = await gh.add_comment({"github_repo": "org/repo"}, "42", "LGTM!")

    assert result["id"] == "9876"
    assert "issuecomment" in result["url"]
    assert captured
    assert captured[0].method == "POST"
    assert "/repos/org/repo/issues/42/comments" in str(captured[0].url)


async def test_github_add_comment_missing_repo_raises():
    """add_comment raises ValueError when github_repo is absent."""
    gh = GitHubIntegration("token123")
    with pytest.raises(ValueError, match="github_repo"):
        await gh.add_comment({}, "42", "body")


pytestmark = pytest.mark.asyncio
