# integrations/github.py — read-only GitHub Issues adapter.
#
# Auth: Personal Access Token (PAT) via Bearer header.
#   Create a PAT at https://github.com/settings/tokens
#   Required scope: repo (for private repos) or public_repo (for public).
#
# API: GitHub REST API v3 (Issues endpoint).
#   GET /repos/{owner}/{repo}/issues?state=all&per_page=100&since=<ISO>
#   Docs: https://docs.github.com/en/rest/issues/issues
#
# Note on pull requests: the GitHub Issues API returns both issues AND pull
# requests (PRs are modelled as issues).  We include both — PRs have
# useful context (description, status).  Callers can filter by checking
# whether item.url contains "/pull/" vs "/issues/".
#
# Pagination: the API returns up to `per_page` items per page.  We page
# until a page comes back with fewer items than the page size (last page).

import httpx

from integrations.base import Item, PMIntegration

_PAGE_SIZE = 100
_API_BASE = "https://api.github.com"


class GitHubIntegration(PMIntegration):
    """Fetch issues (and PRs) from a GitHub repository.

    external_ref key: "github_repo" (e.g. "org/repo" or "user/repo").
    """

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._transport = transport

    def _headers(self) -> dict:
        """Return auth + versioning headers shared by all GitHub API calls."""
        return {
            "Authorization": f"Bearer {self._token}",
            # Request the stable v3 media type so the response shape is stable.
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def fetch_items(
        self,
        external_ref: dict,
        updated_since: str | None = None,
    ) -> list[Item]:
        repo = external_ref.get("github_repo")
        if not repo:
            return []

        headers = self._headers()

        params: dict = {"state": "all", "per_page": _PAGE_SIZE, "page": 1}
        if updated_since:
            # GitHub's `since` param is ISO-8601; our stored timestamps are
            # already in that format.
            params["since"] = updated_since

        items: list[Item] = []

        async with httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            transport=self._transport,
        ) as client:
            while True:
                resp = await client.get(
                    f"{_API_BASE}/repos/{repo}/issues",
                    params=params,
                )
                resp.raise_for_status()
                issues = resp.json()

                if not issues:
                    break

                for issue in issues:
                    assignee = issue.get("assignee") or {}
                    items.append(
                        Item(
                            id=str(issue["number"]),
                            title=issue.get("title", ""),
                            body=issue.get("body") or "",
                            status=issue.get("state", ""),
                            assignee=assignee.get("login") if assignee else None,
                            url=issue.get("html_url", ""),
                            updated_at=issue.get("updated_at", ""),
                            raw=issue,
                        )
                    )

                if len(issues) < _PAGE_SIZE:
                    break
                params["page"] += 1

        return items

    async def add_comment(
        self, external_ref: dict, item_id: str, body: str
    ) -> dict:
        """Post a comment on a GitHub issue or pull request.

        Args:
            external_ref: Must contain "github_repo" (e.g. "org/repo").
            item_id:      The issue/PR number as a string, e.g. "42".
            body:         Markdown-formatted comment body.

        Returns:
            {id, url, created_at} from the GitHub response.
        """
        repo = external_ref.get("github_repo")
        if not repo:
            raise ValueError("external_ref must contain 'github_repo'")
        async with httpx.AsyncClient(
            headers=self._headers(),
            timeout=30.0,
            transport=self._transport,
        ) as client:
            resp = await client.post(
                f"{_API_BASE}/repos/{repo}/issues/{item_id}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
            data = resp.json()
        return {
            "id": str(data["id"]),
            "url": data.get("html_url", ""),
            "created_at": data.get("created_at", ""),
        }
