# sync.py — PM sync orchestrator.
#
# This module owns two SQLite tables:
#
#   sync_state
#   ├── project_id     TEXT   }  composite PK — one row per (project, ref)
#   ├── ref_key        TEXT   }
#   ├── ref_value      TEXT   }
#   └── last_synced_at TEXT      ISO-8601; NULL on first sync
#
#   actions
#   ├── id           TEXT PK   UUID
#   ├── project_id   TEXT
#   ├── action       TEXT      e.g. "sync"
#   ├── status       TEXT      "done" | "failed"
#   ├── payload      TEXT      JSON — item counts, ref details
#   ├── created_at   TEXT
#   └── completed_at TEXT
#
# Why two tables?
#   sync_state answers "when did we last sync ref X?" — looked up before each
#   fetch so we only pull items that changed.
#   actions is an append-only audit log — Step 7 will use it to record
#   proposed writes before a human approves them.  Having it now means the
#   schema is ready and the log starts filling from day one.
#
# Why is sync logic here and not in main.py?
#   Keeping orchestration in its own module makes it unit-testable without
#   a running HTTP server, and keeps main.py as thin route wiring only.

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

import rag
from config import settings
from integrations.base import PMIntegration
from vectors import VectorStore

logger = logging.getLogger("uvicorn.error")


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


async def sync_project(
    project_id: str,
    external_refs: dict,
    sync_store: SyncStore,
    vstore: VectorStore,
    integrations: dict[str, PMIntegration],
) -> list[SyncResult]:
    """Sync all configured PM integrations for one project.

    Args:
        project_id:     The project whose brain to fill.
        external_refs:  project.external_refs dict, e.g.
                        {"jira_project_key": "ALPHA", "github_repo": "org/r"}.
        sync_store:     Manages last_synced_at and the actions audit log.
        vstore:         Qdrant store for ingesting item text as vectors.
        integrations:   Maps ref_key → PMIntegration instance.  Only keys
                        that appear in external_refs AND in this dict are synced.
                        Unconfigured integrations are silently skipped.

    Returns:
        One SyncResult per ref that was synced.
    """
    now = datetime.now(timezone.utc).isoformat()
    results: list[SyncResult] = []

    for ref_key, ref_value in external_refs.items():
        integration = integrations.get(ref_key)
        if integration is None:
            # This ref_key does not have a configured integration — skip.
            # (e.g. the project has a jira_project_key but JIRA_* env vars
            # are not set, so the JiraIntegration was not instantiated.)
            logger.warning(
                "No integration configured for ref_key %r — skipping (project %s)",
                ref_key,
                project_id,
            )
            continue

        last_synced = await sync_store.get_last_synced(
            project_id, ref_key, str(ref_value)
        )

        logger.info(
            "Syncing %s=%r for project %s (last synced: %s)",
            ref_key, ref_value, project_id, last_synced or "never",
        )

        items = await integration.fetch_items(
            {ref_key: ref_value},
            updated_since=last_synced,
        )

        total_chunks = 0
        for item in items:
            # Build a stable source label so repeated syncs of the same item
            # are identifiable and can be deduplicated in the future.
            if ref_key == "jira_project_key":
                source = f"jira:{item.id}"
            else:
                source = f"github:{ref_value}#{item.id}"

            # Delete any existing vectors for this item before re-ingesting.
            # Without this, every sync appends new duplicate chunks instead of
            # replacing the old ones.  Also handles the case where a ticket's
            # body shrank — the old extra chunks disappear cleanly.
            await vstore.delete_by_source(
                settings.qdrant_docs_collection, project_id, source
            )

            # Combine all useful fields into a single text block for ingestion.
            # The LLM will see this text when the chunk is retrieved by RAG.
            text_parts = [item.title, f"Status: {item.status}"]
            if item.assignee:
                text_parts.append(f"Assignee: {item.assignee}")
            if item.body:
                text_parts.append(item.body)
            text_parts.append(f"URL: {item.url}")
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
