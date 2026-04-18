# projects.py — SQLite-backed project store.
#
# Why projects?
#   In Steps 1–4 the agent had one global "brain" — a single conversation
#   history and a single document corpus.  Useful for a demo, but real
#   knowledge workers juggle multiple workstreams (Project Alpha, Project
#   Beta, Project Gamma).  Dumping everything into one brain means:
#     - Answers for Alpha get polluted by unrelated Beta context.
#     - You cannot delete one workstream without wiping everything.
#     - The agent cannot tell you what's happening in one project vs another.
#
#   A "project" here is a small container: a display name, a stable id, and
#   a bag of external references (Jira project key, GitHub repo, ...).  Every
#   message, vector, and ingested document is tagged with the project's id
#   so searches stay scoped.
#
# Schema — two tables owned by this module:
#
#   projects
#   ├── id             TEXT PK   UUID string — stable, unique, client-safe
#   ├── name           TEXT      human-readable label shown in the UI
#   ├── created_at     TEXT      ISO-8601 timestamp set by SQLite on insert
#   └── external_refs  TEXT      JSON map, e.g. {"jira_project_key": "ALPHA"}
#                                Reserved in Step 5; populated in Step 6.
#
#   schema_version
#   ├── version  INTEGER   single-row table; current schema version number.
#
# Why a schema_version table?
#   Step 5 changes the shape of `messages` (it gains project_id).  A running
#   service with a pre-Step-5 database would read the old rows and get NULL
#   for project_id, which would break every query.  Recording the schema
#   version lets startup code detect the mismatch and wipe-and-recreate.
#   (Per owner decision: we wipe existing data rather than migrate.)

import json
import uuid
from dataclasses import dataclass, field

import aiosqlite

# Bump this whenever the SQL schema changes in an incompatible way.
# Step 5 is the first schema version worth recording.
SCHEMA_VERSION = 1


@dataclass
class Project:
    """One project — the container that owns a slice of memory + documents."""
    id: str
    name: str
    created_at: str
    # Free-form adapter references; consumed by Step 6 integrations.
    # Example: {"jira_project_key": "ALPHA", "github_repo": "org/repo"}.
    external_refs: dict = field(default_factory=dict)


class ProjectStore:
    """CRUD for projects + the `schema_version` sentinel table.

    Lives next to ConversationStore in the same SQLite file, so starting up
    the service creates both tables in one place (see main.py lifespan).
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the projects + schema_version tables if they do not exist.

        This is idempotent — safe on every startup.  The schema_version check
        is performed by the caller (main.py) so that any wipe-and-recreate
        logic stays out of this module.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    created_at    TEXT DEFAULT (datetime('now')),
                    external_refs TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
                """
            )
            await db.commit()

    # -----------------------------------------------------------------------
    # Schema version helpers — used by main.py to decide whether to wipe
    # -----------------------------------------------------------------------
    async def current_version(self) -> int | None:
        """Return the recorded schema version, or None if never written."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT version FROM schema_version LIMIT 1")
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_version(self, version: int) -> None:
        """Record the given schema version (overwrites any existing row)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM schema_version")
            await db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            await db.commit()

    # -----------------------------------------------------------------------
    # Project CRUD
    # -----------------------------------------------------------------------
    async def create(self, name: str, external_refs: dict | None = None) -> Project:
        """Insert a new project and return it.

        The id is a random UUID — stable, unique across machines, and safe to
        use in URLs and JSON without escaping.
        """
        project_id = str(uuid.uuid4())
        refs = external_refs or {}
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO projects (id, name, external_refs) VALUES (?, ?, ?)",
                (project_id, name, json.dumps(refs)),
            )
            await db.commit()
            # Fetch the server-assigned created_at so the returned object
            # matches what a later get() would return.
            cursor = await db.execute(
                "SELECT created_at FROM projects WHERE id = ?", (project_id,)
            )
            row = await cursor.fetchone()

        return Project(
            id=project_id,
            name=name,
            created_at=row[0] if row else "",
            external_refs=refs,
        )

    async def list(self) -> list[Project]:
        """Return all projects, newest first.

        Newest-first matches how humans think about active work — the project
        you created most recently is usually the one you're working on now.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, name, created_at, external_refs
                FROM projects
                ORDER BY created_at DESC
                """
            )
            rows = await cursor.fetchall()

        return [
            Project(
                id=row[0],
                name=row[1],
                created_at=row[2],
                external_refs=json.loads(row[3] or "{}"),
            )
            for row in rows
        ]

    async def get(self, project_id: str) -> Project | None:
        """Return one project by id, or None if it does not exist.

        Used by the API layer to validate that an incoming `project_id`
        points at a real project before scoping any read/write against it.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT id, name, created_at, external_refs
                FROM projects
                WHERE id = ?
                """,
                (project_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return Project(
            id=row[0],
            name=row[1],
            created_at=row[2],
            external_refs=json.loads(row[3] or "{}"),
        )

    async def update(
        self,
        project_id: str,
        name: str | None = None,
        external_refs: dict | None = None,
    ) -> Project | None:
        """Update a project's name and/or external_refs.

        Returns the updated project, or None if the id does not exist.
        Unset kwargs are left untouched — this is a partial update, not a
        full replacement.
        """
        existing = await self.get(project_id)
        if existing is None:
            return None

        new_name = name if name is not None else existing.name
        new_refs = external_refs if external_refs is not None else existing.external_refs

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE projects
                SET name = ?, external_refs = ?
                WHERE id = ?
                """,
                (new_name, json.dumps(new_refs), project_id),
            )
            await db.commit()

        return Project(
            id=project_id,
            name=new_name,
            created_at=existing.created_at,
            external_refs=new_refs,
        )

    async def delete(self, project_id: str) -> bool:
        """Delete a project and cascade into its messages.

        Cascade scope:
          - projects row (this table)
          - messages rows with the same project_id (SQLite-side cascade)

        What this does NOT do:
          - Delete Qdrant vectors.  The API layer (main.py) handles that
            separately because this module has no knowledge of Qdrant.
            Keeping ProjectStore SQLite-only makes it trivial to unit-test.

        Returns True if a project was deleted, False if the id was unknown.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM projects WHERE id = ?", (project_id,)
            )
            deleted_projects = cursor.rowcount
            # Cascade: drop any messages tagged with this project.
            # Safe even if the project_id was never used.
            await db.execute(
                "DELETE FROM messages WHERE project_id = ?", (project_id,)
            )
            await db.commit()

        return deleted_projects > 0
