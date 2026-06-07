# tests/test_mcp_integration.py — real mcp-server HTTP integration tests.
#
# These tests call MCPClient.call() against a live mcp-server (port 8083).
# Skipped when the mcp-server is unreachable (conftest.mcp_up).
#
# The mcp-server is started WITHOUT Jira/GitHub credentials, so:
#   - memory_* tools are always available (no creds needed)
#   - jira_* tools raise MCPError because they return isError=true
#
# What is covered:
#   - The real chat-agent ↔ mcp-server HTTP boundary
#   - MCPClient.call() happy path (memory_set / memory_get)
#   - MCPClient.call() isError path (jira_search_issues without creds)

import pytest

from mcp_client import MCPClient, MCPError

pytestmark = pytest.mark.integration


@pytest.fixture
def mcp(mcp_up):
    """MCPClient pointed at the live mcp-server."""
    from config import settings
    return MCPClient(base_url=settings.mcp_base_url, timeout=10.0)


# ─── memory tools (no creds needed) ──────────────────────────────────────────

async def test_memory_set_and_get(mcp):
    """memory_set then memory_get returns the stored value."""
    key = "integration_test_key"
    value = "integration_test_value"

    # Store a value.
    set_result = await mcp.call("memory_set", {"key": key, "value": value})
    # mcp-server returns {} or a confirmation dict — just assert no error raised.
    assert isinstance(set_result, dict)

    # Retrieve the value.
    get_result = await mcp.call("memory_get", {"key": key})
    assert isinstance(get_result, dict)
    # The result should contain the value we stored.
    assert get_result.get("value") == value or value in str(get_result)


async def test_memory_set_overwrites(mcp):
    """Calling memory_set twice with the same key stores the latest value."""
    key = "integration_test_overwrite"

    await mcp.call("memory_set", {"key": key, "value": "first"})
    await mcp.call("memory_set", {"key": key, "value": "second"})

    result = await mcp.call("memory_get", {"key": key})
    assert "second" in str(result)


# ─── tools that require missing vendor credentials ────────────────────────────

async def test_jira_search_raises_mcp_error_without_creds(mcp):
    """jira_search_issues raises MCPError when mcp-server has no Jira credentials.

    This exercises the isError=true → MCPError path in MCPClient.call(),
    which is the code path that main.py's /sync endpoint must handle.
    """
    with pytest.raises(MCPError) as exc_info:
        await mcp.call("jira_search_issues", {"project_key": "TEST", "jql": "project=TEST"})

    # The error message should indicate Jira is not configured.
    err_msg = str(exc_info.value).lower()
    assert any(keyword in err_msg for keyword in ("jira", "not configured", "error", "tool")), (
        f"Unexpected error message: {exc_info.value}"
    )


# ─── health check ─────────────────────────────────────────────────────────────

async def test_mcp_server_health(mcp_up):
    """GET /health returns 200 and {"status":"healthy"}."""
    import httpx
    from config import settings

    async with httpx.AsyncClient() as client:
        r = await client.get(f"{settings.mcp_base_url}/health", timeout=5.0)

    assert r.status_code == 200
    assert r.json().get("status") == "healthy"
