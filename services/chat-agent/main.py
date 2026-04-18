# main.py — FastAPI application entry point.
#
# This file does three things:
#   1. Defines the app lifespan (startup / shutdown hooks + schema checks).
#   2. Declares the Pydantic request/response models for the endpoints.
#   3. Registers the route handlers: /health, /projects (CRUD), /chat,
#      /ingest, /memory/search.
#
# Data flow for POST /chat (Step 5 version):
#   config → projects (validate) → memory (SQLite, scoped by project_id)
#          → embeddings (Ollama) → vectors (Qdrant, filtered by project_id)
#          → rag (document retrieval, scoped by project_id) → llm (DeepSeek)
#
# Each module has one responsibility:
#   config.py     — read env vars once at startup
#   projects.py   — SQLite project store (Step 5+)
#   memory.py     — SQLite conversation store (recent history, per project)
#   embeddings.py — turn text into a 768-float vector via Ollama
#   vectors.py    — store and search vectors in Qdrant, always filtered by project_id
#   rag.py        — chunk + ingest documents; retrieve relevant chunks (per project)
#   llm.py        — send a message list to DeepSeek, get a reply

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config import settings
from embeddings import embed
from llm import chat
from memory import ConversationStore
from projects import SCHEMA_VERSION, Project, ProjectStore
import rag
from vectors import VectorStore

# Use uvicorn's own logger so our INFO messages appear alongside uvicorn's
# access log output.  This works whether running via uvicorn CLI or docker.
logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Shared state — one instance of each store for the whole process.
# ---------------------------------------------------------------------------
project_store = ProjectStore(settings.sqlite_path)
store = ConversationStore(settings.sqlite_path)
vstore = VectorStore(url=settings.qdrant_url)

# nomic-embed-text always produces 768-dimensional vectors.
# This must match the dimension used when the Qdrant collections were created.
EMBED_DIM = 768


# ---------------------------------------------------------------------------
# Lifespan — startup initialisation + one-time schema wipe.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the projects + schema_version tables exist BEFORE we check the
    # version — otherwise current_version() would fail on a blank DB.
    await project_store.init()

    previous_version = await project_store.current_version()
    schema_mismatch = previous_version != SCHEMA_VERSION

    if schema_mismatch:
        # Step 5 decision: wipe rather than migrate.  This runs once when
        # upgrading from a pre-Step-5 DB (previous_version is None) and again
        # any future time we bump SCHEMA_VERSION.  Loud log line on purpose.
        logger.warning(
            "Schema version mismatch (stored=%r, code=%r) — wiping messages "
            "and Qdrant collections.  Project rows are preserved.",
            previous_version,
            SCHEMA_VERSION,
        )
        await store.reset()
        await vstore.reset_collection(settings.qdrant_collection, dim=EMBED_DIM)
        await vstore.reset_collection(settings.qdrant_docs_collection, dim=EMBED_DIM)
        await project_store.set_version(SCHEMA_VERSION)
    else:
        # Normal boot: just make sure everything is in place.  Idempotent.
        await store.init()
        await vstore.ensure_collection(settings.qdrant_collection, dim=EMBED_DIM)
        await vstore.ensure_collection(settings.qdrant_docs_collection, dim=EMBED_DIM)

    yield
    # Shutdown: no explicit cleanup needed for either store.


app = FastAPI(title="chat-agent", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow the Chrome extension (and any localhost origin) to call this API.
#
# Why allow_origins=["*"]?
#   Chrome extension IDs look like chrome-extension://abcdef123456...  The ID
#   changes every time an unpacked extension is reloaded in developer mode, so
#   we cannot hard-code it.  Since this server only listens on localhost and is
#   never exposed to the internet, allowing all origins is safe.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

# --- Project CRUD models ---------------------------------------------------
class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Human-readable project name.")
    # Optional bag of external references (Jira key, GitHub repo, ...).
    # Reserved in Step 5; populated by Step 6 integrations.
    external_refs: dict = Field(default_factory=dict)


class ProjectUpdateRequest(BaseModel):
    # Both fields optional — callers send only what they want to change.
    name: str | None = None
    external_refs: dict | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: str
    external_refs: dict


def _project_to_out(p: Project) -> ProjectOut:
    """Small helper — turning dataclass into pydantic one place."""
    return ProjectOut(
        id=p.id,
        name=p.name,
        created_at=p.created_at,
        external_refs=p.external_refs,
    )


# --- Chat / ingest models (all now require project_id) --------------------
class ChatRequest(BaseModel):
    project_id: str   # Which project brain owns this conversation.
    session_id: str   # Identifies which conversation inside the project.
    message: str      # The user's input text.


class ChatResponse(BaseModel):
    reply: str        # The assistant's reply text.


class IngestRequest(BaseModel):
    project_id: str   # Which project brain to add this document to.
    source: str       # A label for the document — filename, URL, or any identifier.
    text: str         # The full document text to index.


class IngestResponse(BaseModel):
    chunks: int       # How many chunks were stored in Qdrant.


class MemoryHit(BaseModel):
    score: float      # Cosine similarity (0–1, higher = more similar).
    role: str         # 'user' or 'assistant'.
    content: str      # The original message text.
    session_id: str   # Which conversation the hit came from.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _require_project(project_id: str) -> Project:
    """Return the project, or raise 404.

    Every scoped endpoint starts by calling this so "unknown project" fails
    loudly rather than silently returning empty results.
    """
    project = await project_store.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id!r} not found.",
        )
    return project


# ---------------------------------------------------------------------------
# Routes — liveness
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Liveness check.  Returns 200 OK when the service is running."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Routes — project CRUD
# ---------------------------------------------------------------------------
@app.post("/projects", response_model=ProjectOut)
async def post_projects(req: ProjectCreateRequest) -> ProjectOut:
    """Create a new project brain.

    The returned id is a UUID string — stable, safe to use in URLs/JSON.
    """
    project = await project_store.create(
        name=req.name,
        external_refs=req.external_refs,
    )
    logger.info("Created project %r (%s)", project.name, project.id)
    return _project_to_out(project)


@app.get("/projects", response_model=list[ProjectOut])
async def get_projects() -> list[ProjectOut]:
    """List all projects, newest first."""
    projects = await project_store.list()
    return [_project_to_out(p) for p in projects]


@app.patch("/projects/{project_id}", response_model=ProjectOut)
async def patch_project(project_id: str, req: ProjectUpdateRequest) -> ProjectOut:
    """Partial update — change name and/or external_refs.

    Step 6 uses this to attach a Jira project key or GitHub repo to a brain.
    """
    updated = await project_store.update(
        project_id,
        name=req.name,
        external_refs=req.external_refs,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    return _project_to_out(updated)


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and cascade through all of its state.

    Cascade:
      - SQLite:  projects row + messages rows   (ProjectStore.delete)
      - Qdrant:  conversations + documents points tagged with project_id
    """
    # Do the vector deletes first.  If ProjectStore.delete() succeeded but the
    # Qdrant delete failed, we'd be left with orphan vectors filtered on a
    # project id that no longer exists — confusing but not dangerous.  Doing
    # Qdrant first means a failure there prevents the SQLite delete, and the
    # user can retry.
    await vstore.delete_by_project(settings.qdrant_collection, project_id)
    await vstore.delete_by_project(settings.qdrant_docs_collection, project_id)

    deleted = await project_store.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    logger.info("Deleted project %s and all of its memory.", project_id)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Routes — chat + ingest + memory search (now project-scoped)
# ---------------------------------------------------------------------------
@app.post("/ingest", response_model=IngestResponse)
async def post_ingest(req: IngestRequest) -> IngestResponse:
    """Split a document into chunks, embed them, and store them in Qdrant
    under the given project.

    Args (JSON body):
        project_id: Which project brain owns this document.
        source:     A label for this document (e.g. a filename or URL).
        text:       The full document text.

    Returns:
        {"chunks": N} — the number of chunks stored.
    """
    await _require_project(req.project_id)
    n = await rag.ingest(req.project_id, req.source, req.text, vstore)
    return IngestResponse(chunks=n)


@app.post("/chat", response_model=ChatResponse)
async def post_chat(req: ChatRequest) -> ChatResponse:
    """Accept a user message, retrieve scoped memory + documents, call DeepSeek.

    Full flow (everything scoped by project_id):
        1.  Load the last 6 messages for (project, session) from SQLite.
        2.  Persist the new user message to SQLite.
        3.  Embed the user message; search Qdrant conversations within
            the project for similar past messages.
        3b. Search Qdrant documents within the project for relevant chunks.
        4.  Upsert the user message vector into the conversations collection
            (tagged with project_id).
        5.  Build the prompt from document chunks, memory hits, recent history.
        6.  Call DeepSeek.
        7.  Persist the assistant reply to SQLite (tagged with project_id).
        8.  Upsert the assistant reply vector into conversations (tagged).
        9.  Return the reply.
    """
    await _require_project(req.project_id)

    # 1. Recent history (last 6 messages = 3 turns, oldest first).
    recent = await store.history(req.project_id, req.session_id, limit=6)

    # 2. Persist the user message to SQLite immediately.
    await store.append(req.project_id, req.session_id, "user", req.message)

    # 3. Embed + search conversations collection for similar past messages,
    #    filtered to this project only.
    query_vec = await embed(req.message)
    hits = await vstore.search(
        settings.qdrant_collection,
        project_id=req.project_id,
        vector=query_vec,
        k=settings.memory_search_k,
    )

    # 3b. Search documents collection for relevant chunks (RAG), same filter.
    doc_chunks = await rag.retrieve(req.project_id, req.message, k=3, vstore=vstore)
    logger.info(
        "RAG[%s]: retrieved %d doc chunks for query %r: %s",
        req.project_id,
        len(doc_chunks),
        req.message,
        [(c.source, c.chunk_index) for c in doc_chunks],
    )

    # 4. Store the user message vector in Qdrant conversations.
    await vstore.upsert(
        settings.qdrant_collection,
        project_id=req.project_id,
        vector=query_vec,
        payload={"session_id": req.session_id, "role": "user", "content": req.message},
    )

    # 5. Build the prompt.
    #
    # Deduplication for conversation hits: drop any hit already in recent history.
    recent_contents = {m["content"] for m in recent}
    unique_hits = [
        h for h in hits
        if h.session_id == req.session_id and h.content not in recent_contents
    ]

    messages: list[dict] = []

    # Inject relevant document chunks first (highest priority context).
    # Only include chunks with a reasonable similarity score (> 0.5).
    # Deduplicate by text content — the same chunk may appear multiple times
    # if the same document was ingested more than once.
    seen_texts: set[str] = set()
    relevant_chunks = []
    for c in doc_chunks:
        if c.score > 0.5 and c.text not in seen_texts:
            seen_texts.add(c.text)
            relevant_chunks.append(c)
    if relevant_chunks:
        doc_lines = "\n".join(
            f"- [{c.source}]: {c.text}" for c in relevant_chunks
        )
        messages.append({
            "role": "system",
            "content": (
                "The following excerpts from ingested documents may be relevant "
                "to the user's question:\n" + doc_lines
            ),
        })

    # Then inject relevant past conversation messages.
    if unique_hits:
        context_lines = "\n".join(
            f"- [{h.role}]: {h.content}" for h in unique_hits
        )
        messages.append({
            "role": "system",
            "content": (
                "The following messages from earlier in this conversation may be "
                "relevant to the user's current question:\n" + context_lines
            ),
        })

    # Append recent history and the new user message.
    messages += recent + [{"role": "user", "content": req.message}]

    # 6. Call DeepSeek.
    reply = await chat(messages)

    # 7. Persist the assistant reply to SQLite.
    await store.append(req.project_id, req.session_id, "assistant", reply)

    # 8. Store the assistant reply vector in Qdrant conversations.
    reply_vec = await embed(reply)
    await vstore.upsert(
        settings.qdrant_collection,
        project_id=req.project_id,
        vector=reply_vec,
        payload={"session_id": req.session_id, "role": "assistant", "content": reply},
    )

    # 9. Return.
    return ChatResponse(reply=reply)


@app.get("/memory/search", response_model=list[MemoryHit])
async def memory_search(
    project_id: str = Query(..., description="Restrict search to this project."),
    q: str = Query(..., description="The text to search for in vector memory."),
    k: int = Query(5, ge=1, le=20, description="Number of results to return."),
) -> list[MemoryHit]:
    """Search conversation vector memory (within one project) for messages
    semantically similar to q.

    This is a debug / inspection endpoint — it lets you see what the agent
    would retrieve as context for a given query without making a full chat call.

    Example:
        GET /memory/search?project_id=<uuid>&q=what+is+my+name&k=3
    """
    await _require_project(project_id)

    vec = await embed(q)
    hits = await vstore.search(
        settings.qdrant_collection,
        project_id=project_id,
        vector=vec,
        k=k,
    )
    return [
        MemoryHit(
            score=h.score,
            role=h.role,
            content=h.content,
            session_id=h.session_id,
        )
        for h in hits
    ]
