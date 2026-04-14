# memory.py — SQLite-backed conversation store.
#
# Why SQLite?
#   An in-memory dict would work while the server is running, but the moment
#   the process restarts all history is gone.  SQLite writes to a file on disk,
#   so history survives restarts and (in Docker) can be stored on a mounted volume.
#
# Why aiosqlite?
#   FastAPI runs on an async event loop (asyncio).  The standard sqlite3 module
#   makes blocking system calls — while waiting for a disk write, it would freeze
#   the entire event loop and block every other request.  aiosqlite wraps sqlite3
#   in a background thread and gives us `await`-able versions of every call.
#
# Schema — one table, five columns:
#
#   messages
#   ├── id          INTEGER  auto-incrementing primary key
#   ├── session_id  TEXT     groups messages into one conversation
#   ├── role        TEXT     'user' or 'assistant'  (DeepSeek's terminology)
#   ├── content     TEXT     the message body
#   └── created_at  TEXT     ISO-8601 timestamp set by SQLite on insert

import aiosqlite


class ConversationStore:
    """Stores and retrieves chat messages for named sessions."""

    def __init__(self, db_path: str) -> None:
        # db_path comes from settings.sqlite_path so tests can pass a temp path.
        self.db_path = db_path

    async def init(self) -> None:
        """Create the messages table if it does not already exist.

        Called once at server startup (see main.py lifespan).  Using
        IF NOT EXISTS means it is safe to call on every restart.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT    NOT NULL,
                    role       TEXT    NOT NULL,
                    content    TEXT    NOT NULL,
                    created_at TEXT    DEFAULT (datetime('now'))
                )
                """
            )
            # An index on session_id makes history() fast even with many rows.
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages (session_id)
                """
            )
            await db.commit()

    async def append(self, session_id: str, role: str, content: str) -> None:
        """Insert one message into the store.

        Args:
            session_id: Arbitrary string that groups messages into a conversation.
            role:       'user' or 'assistant'.
            content:    The message text.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            await db.commit()

    async def history(self, session_id: str, limit: int = 20) -> list[dict]:
        """Return the last `limit` messages for a session, oldest first.

        Returns a list of {"role": ..., "content": ...} dicts — exactly the
        format the DeepSeek (OpenAI-compatible) chat completions API expects.

        Why oldest-first?  The LLM reads the conversation in chronological order,
        just as a human would.  Reversing the list would confuse it.

        Why a limit?  Sending thousands of old messages would overflow the model's
        context window and cost more tokens.  20 messages covers most conversations.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Fetch the last N rows by id descending, then reverse to get
            # chronological order.
            cursor = await db.execute(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, id
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (session_id, limit),
            )
            rows = await cursor.fetchall()

        # Convert tuples to the dict shape the LLM client expects.
        return [{"role": row[0], "content": row[1]} for row in rows]
