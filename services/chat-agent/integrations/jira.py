# integrations/jira.py — read-only Jira Cloud adapter.
#
# Auth: HTTP Basic with email + API token.
#   Jira Cloud does not accept password auth; you need an API token from
#   https://id.atlassian.com/manage-profile/security/api-tokens
#
# API: Jira Cloud REST v3 (the current version; v2 is deprecated).
#   Docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
#
# Description format: Jira v3 returns the `description` field as Atlassian
# Document Format (ADF) — a JSON tree, not a plain string.  We walk the tree
# and concatenate all `text` leaf nodes.  The result won't preserve rich
# formatting but is good enough for RAG ingestion.
#
# Pagination: /search uses startAt + maxResults.  We page until fewer than
# maxResults issues are returned (last page).

from datetime import datetime, timezone

import logging

import httpx

from integrations.base import Item, PMIntegration

logger = logging.getLogger("uvicorn.error")

# How many issues to fetch per API call.  Jira's hard cap is 100.
_PAGE_SIZE = 100


def _extract_adf_text(node: dict) -> str:
    """Recursively extract plain text from an Atlassian Document Format node.

    ADF is a tree where leaves have type="text" and a "text" field.  All
    other node types (paragraph, heading, bulletList, ...) only have a
    "content" list of child nodes.  We walk the whole tree and join the
    leaf text with newlines between block-level nodes.
    """
    if node.get("type") == "text":
        return node.get("text", "")
    children = node.get("content", [])
    parts = [_extract_adf_text(child) for child in children]
    # Drop empty strings so we don't get blank lines from empty containers.
    return "\n".join(p for p in parts if p)


def _jira_date(iso: str) -> str:
    """Convert ISO-8601 (e.g. '2024-01-15T10:30:00Z') to Jira JQL date format.

    Jira's JQL date parser accepts 'YYYY-MM-DD HH:mm'.  We strip seconds and
    timezone info because Jira interprets the date in the project's timezone.
    """
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        # If parsing fails, pass the string through and let Jira reject it.
        return iso


class JiraIntegration(PMIntegration):
    """Fetch issues from a Jira Cloud project.

    external_ref key: "jira_project_key" (e.g. "ALPHA").
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # base_url looks like "https://your-org.atlassian.net"
        self._base_url = base_url.rstrip("/")
        # httpx Basic auth tuple: (username, password)
        self._auth = (email, api_token)
        # Injected transport for unit tests; None means real network.
        self._transport = transport

    async def fetch_items(
        self,
        external_ref: dict,
        updated_since: str | None = None,
    ) -> list[Item]:
        key = external_ref.get("jira_project_key")
        if not key:
            return []

        jql = f"project = {key} ORDER BY updated ASC"
        if updated_since:
            jql = (
                f'project = {key} AND updated >= "{_jira_date(updated_since)}" '
                f"ORDER BY updated ASC"
            )

        items: list[Item] = []
        start_at = 0

        async with httpx.AsyncClient(
            auth=self._auth,
            timeout=30.0,
            transport=self._transport,
        ) as client:
            # The new /search/jql API uses cursor-based pagination via
            # nextPageToken.  We loop until the response contains no token.
            next_page_token: str | None = None
            while True:
                body: dict = {
                    "jql": jql,
                    "fields": ["summary", "description", "status", "assignee", "updated"],
                    "maxResults": _PAGE_SIZE,
                }
                if next_page_token:
                    body["nextPageToken"] = next_page_token

                resp = await client.post(
                    f"{self._base_url}/rest/api/3/search/jql",
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                issues = data.get("issues", [])

                for issue in issues:
                    fields = issue["fields"]

                    desc = fields.get("description") or ""
                    if isinstance(desc, dict):
                        # Jira v3 ADF object — extract plain text.
                        desc = _extract_adf_text(desc)

                    assignee_node = fields.get("assignee") or {}
                    items.append(
                        Item(
                            id=issue["key"],
                            title=fields.get("summary", ""),
                            body=desc,
                            status=fields.get("status", {}).get("name", ""),
                            assignee=assignee_node.get("displayName"),
                            url=f"{self._base_url}/browse/{issue['key']}",
                            updated_at=fields.get("updated", ""),
                            raw=issue,
                        )
                    )

                next_page_token = data.get("nextPageToken")
                if not next_page_token or len(issues) < _PAGE_SIZE:
                    break

        return items
