# PM sync orchestrator.
#
# Why two tables?
#   sync_state answers "when did we last sync ref X?" — looked up before each
#   fetch so we only pull items that changed.
#   actions is an append-only audit log.  Having it now means the
#   schema is ready and the log starts filling from day one.
#
# Why is sync logic here and not in main.py?
#   Keeping orchestration in its own module makes it unit-testable without
#   a running HTTP server, and keeps main.py as thin route wiring only.
#
# PM fetch strategy:
#   All vendor API calls go through the mcp-server (MCPClient).
#   jira_project_key → jira_search_issues (list) + jira_get_issue (hydrate each)
#   github_repo      → github_list_issues (list) + github_get_issue (hydrate each)
#   Unknown ref_keys are skipped with a warning.
#
# Hydration: the list tools return minimal stubs (key/summary/state only).
#   We call the get-detail tool per item to obtain description and updated_at.
#   Cap: 50 Jira issues / 100 GitHub issues per sync call.

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

import rag
from config import settings
from mcp_client import MCPClient, MCPError
from vectors import VectorStore

logger = logging.getLogger("uvicorn.error")

_JIRA_MAX_RESULTS = 50
_GITHUB_MAX_RESULTS = 100


@dataclass
class SyncResult:
    """Result of syncing one external ref for one project."""
    ref_key: str
    ref_value: str
    items_fetched: int
    chunks_stored: int


class SyncStore:
    """Manages sync_state and actions tables.

    Lives in the same SQLite file as projects + messages so we need only one
    database file and get cross-table transactions for free.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create tables if they do not exist.  Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    project_id     TEXT NOT NULL,
                    ref_key        TEXT NOT NULL,
                    ref_value      TEXT NOT NULL,
                    last_synced_at TEXT,
                    PRIMARY KEY (project_id, ref_key, ref_value)
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id           TEXT PRIMARY KEY,
                    project_id   TEXT NOT NULL,
                    action       TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'done',
                    payload      TEXT NOT NULL DEFAULT '{}',
                    created_at   TEXT DEFAULT (datetime('now')),
                    completed_at TEXT
                )
                """
            )
            await db.commit()

    async def get_last_synced(
        self, project_id: str, ref_key: str, ref_value: str
    ) -> str | None:
        """Return the last-synced ISO timestamp for this (project, ref) pair,
        or None if it has never been synced."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT last_synced_at FROM sync_state
                WHERE project_id = ? AND ref_key = ? AND ref_value = ?
                """,
                (project_id, ref_key, ref_value),
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_last_synced(
        self, project_id: str, ref_key: str, ref_value: str, timestamp: str
    ) -> None:
        """Upsert the last-synced timestamp for this (project, ref) pair."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sync_state (project_id, ref_key, ref_value, last_synced_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (project_id, ref_key, ref_value)
                DO UPDATE SET last_synced_at = excluded.last_synced_at
                """,
                (project_id, ref_key, ref_value, timestamp),
            )
            await db.commit()

    async def record_action(
        self, project_id: str, action: str, payload: dict
    ) -> None:
        """Append an audit row to the actions table."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO actions (id, project_id, action, status, payload, completed_at)
                VALUES (?, ?, ?, 'done', ?, datetime('now'))
                """,
                (str(uuid.uuid4()), project_id, action, json.dumps(payload)),
            )
            await db.commit()

    async def get_sync_status(self, project_id: str) -> list[dict]:
        """Return all sync_state rows for a project (for the GET /sync endpoint)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT ref_key, ref_value, last_synced_at
                FROM sync_state
                WHERE project_id = ?
                ORDER BY ref_key, ref_value
                """,
                (project_id,),
            )
            rows = await cursor.fetchall()
        return [
            {"ref_key": r[0], "ref_value": r[1], "last_synced_at": r[2]}
            for r in rows
        ]

    async def delete_by_project(self, project_id: str) -> None:
        """Remove all sync state for a project (called when the project is deleted)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM sync_state WHERE project_id = ?", (project_id,)
            )
            await db.execute(
                "DELETE FROM actions WHERE project_id = ?", (project_id,)
            )
            await db.commit()


# ---------------------------------------------------------------------------
# PM fetch helpers — one per supported ref_key type
# ---------------------------------------------------------------------------

async def _fetch_jira(
    mcp: MCPClient, project_key: str, updated_since: str | None
) -> list[dict]:
    """Fetch Jira issues via jira_search_issues + jira_get_issue per key.

    Returns a list of normalised item dicts with keys:
    id, title, body, status, assignee, url, updated_at.
    """
    jql = f"project = {project_key} ORDER BY updated ASC"
    if updated_since:
        jql_date = _jira_date(updated_since)
        jql = f'project = {project_key} AND updated >= "{jql_date}" ORDER BY updated ASC'

    result = await mcp.call(
        "jira_search_issues",
        {"query": jql, "max_results": _JIRA_MAX_RESULTS},
    )
    stubs = result.get("issues", [])
    if not stubs:
        return []

    items: list[dict] = []
    for stub in stubs:
        key = stub.get("key", "")
        if not key:
            continue
        try:
            detail = await mcp.call("jira_get_issue", {"key": key})
        except MCPError as exc:
            logger.warning("jira_get_issue(%s) failed: %s — using stub data", key, exc)
            detail = {
                "key": key,
                "summary": stub.get("summary", ""),
                "description": "",
                "status": stub.get("status", ""),
                "assignee": stub.get("assignee", ""),
                "url": stub.get("url", ""),
                "updated": "",
            }
        items.append({
            "id": detail.get("key", key),
            "title": detail.get("summary", ""),
            "body": detail.get("description") or "",
            "status": detail.get("status", ""),
            "assignee": detail.get("assignee") or None,
            "url": detail.get("url", ""),
            "updated_at": detail.get("updated", ""),
        })
    return items


async def _fetch_github(
    mcp: MCPClient, repo: str, updated_since: str | None
) -> list[dict]:
    """Fetch GitHub issues via github_list_issues + github_get_issue per number.

    The list tool has no `since` parameter, so we filter client-side using
    created_at as a proxy.  Items created before `updated_since` are skipped;
    items updated-but-not-recreated after that cutoff will be re-fetched anyway
    because we always fetch state=all and the issue stays in the list.
    """
    result = await mcp.call(
        "github_list_issues",
        {"repo": repo, "state": "all", "limit": _GITHUB_MAX_RESULTS},
    )
    stubs = result.get("issues", [])
    if not stubs:
        return []

    if updated_since:
        since_dt = _parse_dt(updated_since)
        stubs = [s for s in stubs if _parse_dt(s.get("created_at", "")) >= since_dt]

    items: list[dict] = []
    for stub in stubs:
        number = stub.get("number")
        if number is None:
            continue
        try:
            detail = await mcp.call("github_get_issue", {"repo": repo, "number": number})
        except MCPError as exc:
            logger.warning(
                "github_get_issue(%s#%s) failed: %s — using stub data", repo, number, exc
            )
            detail = {
                "number": number,
                "title": stub.get("title", ""),
                "body": "",
                "state": stub.get("state", ""),
                "url": stub.get("url", ""),
                "updated_at": stub.get("created_at", ""),
            }
        items.append({
            "id": str(detail.get("number", number)),
            "title": detail.get("title", ""),
            "body": detail.get("body") or "",
            "status": detail.get("state", ""),
            "assignee": detail.get("assignee") or None,
            "url": detail.get("url", ""),
            "updated_at": detail.get("updated_at", ""),
        })
    return items


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def sync_project(
    project_id: str,
    external_refs: dict,
    sync_store: SyncStore,
    vstore: VectorStore,
    mcp: MCPClient,
) -> list[SyncResult]:
    """Sync all configured PM refs for one project through the mcp-server.

    Args:
        project_id:    The project whose brain to fill.
        external_refs: project.external_refs dict, e.g.
                       {"jira_project_key": "ALPHA", "github_repo": "org/r"}.
        sync_store:    Manages last_synced_at and the actions audit log.
        vstore:        Qdrant store for ingesting item text as vectors.
        mcp:           Shared MCPClient — the single gateway to all PM vendor APIs.

    Returns:
        One SyncResult per ref that was synced.
    """
    now = datetime.now(timezone.utc).isoformat()
    results: list[SyncResult] = []

    for ref_key, ref_value in external_refs.items():
        last_synced = await sync_store.get_last_synced(
            project_id, ref_key, str(ref_value)
        )

        logger.info(
            "Syncing %s=%r for project %s (last synced: %s)",
            ref_key, ref_value, project_id, last_synced or "never",
        )

        if ref_key == "jira_project_key":
            items = await _fetch_jira(mcp, str(ref_value), last_synced)
        elif ref_key == "github_repo":
            items = await _fetch_github(mcp, str(ref_value), last_synced)
        else:
            logger.warning(
                "No PM tool configured for ref_key %r — skipping (project %s)",
                ref_key, project_id,
            )
            continue

        total_chunks = 0
        for item in items:
            if ref_key == "jira_project_key":
                source = f"jira:{item['id']}"
            else:
                source = f"github:{ref_value}#{item['id']}"

            # Delete existing vectors before re-ingesting so repeated syncs
            # replace old chunks rather than accumulating duplicates.
            await vstore.delete_by_source(
                settings.qdrant_docs_collection, project_id, source
            )

            text_parts = [item["title"], f"Status: {item['status']}"]
            if item.get("assignee"):
                text_parts.append(f"Assignee: {item['assignee']}")
            if item.get("body"):
                text_parts.append(item["body"])
            text_parts.append(f"URL: {item['url']}")
            text = "\n\n".join(text_parts)

            n = await rag.ingest(project_id, source, text, vstore)
            total_chunks += n

        await sync_store.set_last_synced(
            project_id, ref_key, str(ref_value), now
        )
        await sync_store.record_action(
            project_id,
            "sync",
            {
                "ref_key": ref_key,
                "ref_value": ref_value,
                "items_fetched": len(items),
                "chunks_stored": total_chunks,
            },
        )

        logger.info(
            "Sync complete: %s=%r → %d items, %d chunks (project %s)",
            ref_key, ref_value, len(items), total_chunks, project_id,
        )
        results.append(
            SyncResult(
                ref_key=ref_key,
                ref_value=str(ref_value),
                items_fetched=len(items),
                chunks_stored=total_chunks,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _jira_date(iso: str) -> str:
    """Convert ISO-8601 string to Jira JQL date format (YYYY-MM-DD HH:mm)."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _parse_dt(iso: str) -> datetime:
    """Parse ISO-8601 to a UTC-aware datetime; returns datetime.min on failure."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return datetime.min.replace(tzinfo=timezone.utc)
