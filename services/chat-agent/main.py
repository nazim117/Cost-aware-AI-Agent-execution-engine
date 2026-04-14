# main.py — FastAPI application entry point.
#
# This file does three things:
#   1. Defines the app lifespan (startup / shutdown hooks).
#   2. Declares the Pydantic request/response models for the endpoints.
#   3. Registers the route handlers: GET /health, POST /chat, POST /ingest,
#      GET /memory/search.
#
# Data flow for POST /chat (Step 3 version):
#   config → memory (SQLite) → embeddings (Ollama) → vectors (Qdrant)
#          → rag (document retrieval) → llm (DeepSeek)
#
# Each module has one responsibility:
#   config.py     — read env vars once at startup
#   memory.py     — SQLite conversation store (recent history)
#   embeddings.py — turn text into a 768-float vector via Ollama
#   vectors.py    — store and search those vectors in Qdrant
#   rag.py        — chunk + ingest documents; retrieve relevant chunks
#   llm.py        — send a message list to DeepSeek, get a reply

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import settings
from embeddings import embed
from llm import chat
from memory import ConversationStore
import rag
from vectors import VectorStore

# Use uvicorn's own logger so our INFO messages appear alongside uvicorn's
# access log output.  This works whether running via uvicorn CLI or docker.
logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Shared state — one instance of each store for the whole process.
# ---------------------------------------------------------------------------
store = ConversationStore(settings.sqlite_path)
vstore = VectorStore(url=settings.qdrant_url)

# nomic-embed-text always produces 768-dimensional vectors.
# This must match the dimension used when the Qdrant collection was created.
EMBED_DIM = 768


# ---------------------------------------------------------------------------
# Lifespan — startup initialisation for all stores.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise SQLite (creates table if not present).
    await store.init()
    # Initialise Qdrant collections (idempotent — safe to call on every restart).
    # If Qdrant is unreachable these will raise immediately — fail fast.
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
class ChatRequest(BaseModel):
    session_id: str   # Identifies which conversation this message belongs to.
    message: str      # The user's input text.


class ChatResponse(BaseModel):
    reply: str        # The assistant's reply text.


class IngestRequest(BaseModel):
    source: str   # A label for the document — filename, URL, or any identifier.
    text: str     # The full document text to index.


class IngestResponse(BaseModel):
    chunks: int   # How many chunks were stored in Qdrant.


class MemoryHit(BaseModel):
    score: float      # Cosine similarity (0–1, higher = more similar).
    role: str         # 'user' or 'assistant'.
    content: str      # The original message text.
    session_id: str   # Which conversation the hit came from.


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Liveness check.  Returns 200 OK when the service is running."""
    return {"status": "ok"}


@app.post("/ingest", response_model=IngestResponse)
async def post_ingest(req: IngestRequest) -> IngestResponse:
    """Split a document into chunks, embed them, and store them in Qdrant.

    The stored chunks are searched on every subsequent POST /chat call, so
    the agent's replies will be grounded in the ingested content.

    Args (JSON body):
        source: A label for this document (e.g. a filename or URL).
        text:   The full document text.

    Returns:
        {"chunks": N} — the number of chunks stored.
    """
    n = await rag.ingest(req.source, req.text, vstore)
    return IngestResponse(chunks=n)


@app.post("/chat", response_model=ChatResponse)
async def post_chat(req: ChatRequest) -> ChatResponse:
    """Accept a user message, retrieve relevant memory + documents, call DeepSeek.

    Full flow:
        1.  Load the last 6 messages for this session from SQLite (recent context).
        2.  Persist the new user message to SQLite.
        3.  Embed the user message; search Qdrant conversations for similar past msgs.
        3b. Search Qdrant documents for relevant document chunks (RAG).
        4.  Upsert the user message vector into the conversations collection.
        5.  Build the prompt:
              [system: relevant document excerpts]   ← from step 3b
              [system: relevant conversation memory] ← from step 3
              [recent SQLite history]                ← from step 1
              [new user message]
        6.  Call DeepSeek.
        7.  Persist the assistant reply to SQLite.
        8.  Upsert the assistant reply vector into the conversations collection.
        9.  Return the reply.
    """
    # 1. Recent history (last 6 messages = 3 turns, oldest first).
    recent = await store.history(req.session_id, limit=6)

    # 2. Persist the user message to SQLite immediately.
    await store.append(req.session_id, "user", req.message)

    # 3. Embed + search conversations collection for similar past messages.
    query_vec = await embed(req.message)
    hits = await vstore.search(
        settings.qdrant_collection,
        vector=query_vec,
        k=settings.memory_search_k,
    )

    # 3b. Search documents collection for relevant chunks (RAG).
    doc_chunks = await rag.retrieve(req.message, k=3, vstore=vstore)
    logger.info(
        "RAG: retrieved %d doc chunks for query %r: %s",
        len(doc_chunks),
        req.message,
        [(c.source, c.chunk_index) for c in doc_chunks],
    )

    # 4. Store the user message vector in Qdrant conversations.
    await vstore.upsert(
        settings.qdrant_collection,
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
    await store.append(req.session_id, "assistant", reply)

    # 8. Store the assistant reply vector in Qdrant conversations.
    reply_vec = await embed(reply)
    await vstore.upsert(
        settings.qdrant_collection,
        vector=reply_vec,
        payload={"session_id": req.session_id, "role": "assistant", "content": reply},
    )

    # 9. Return.
    return ChatResponse(reply=reply)


@app.get("/memory/search", response_model=list[MemoryHit])
async def memory_search(
    q: str = Query(..., description="The text to search for in vector memory."),
    k: int = Query(5, ge=1, le=20, description="Number of results to return."),
) -> list[MemoryHit]:
    """Search conversation vector memory for messages semantically similar to q.

    This is a debug / inspection endpoint — it lets you see what the agent
    would retrieve as context for a given query without making a full chat call.

    Example:
        GET /memory/search?q=what+is+my+name&k=3
    """
    vec = await embed(q)
    hits = await vstore.search(settings.qdrant_collection, vector=vec, k=k)
    return [
        MemoryHit(
            score=h.score,
            role=h.role,
            content=h.content,
            session_id=h.session_id,
        )
        for h in hits
    ]
