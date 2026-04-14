# vectors.py — Qdrant vector database client.
#
# What is Qdrant?
#   Qdrant is a vector database: a database optimised for storing and searching
#   high-dimensional vectors.  A regular database like SQLite can find rows WHERE
#   content = 'hello' (exact match).  Qdrant finds rows WHERE vector ≈ query_vector
#   (similarity match) — it returns the most semantically similar stored items.
#
# What is cosine similarity?
#   The similarity score Qdrant returns is cosine similarity: a number between
#   -1 and 1 that measures the angle between two vectors.
#   - 1.0  → identical direction  → semantically the same text
#   - 0.0  → perpendicular        → unrelated text
#   - -1.0 → opposite direction   → antonyms (rare in practice)
#   In practice, scores above ~0.85 are very similar; below ~0.5 are unrelated.
#
# Data model:
#   Each stored "point" in Qdrant has three parts:
#   - id:      a UUID string that uniquely identifies this point
#   - vector:  the 768-float embedding of the message text
#   - payload: a JSON dict with {session_id, role, content} so we can
#              reconstruct the original message from search results

from dataclasses import dataclass, field
from uuid import uuid4

from fastapi import HTTPException
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from config import settings


@dataclass
class Hit:
    """One result from a Qdrant similarity search."""
    score: float             # cosine similarity score (higher = more similar)
    session_id: str          # which conversation this message belongs to
    role: str                # 'user' or 'assistant'
    content: str             # the original message text
    # Raw Qdrant payload — contains all stored fields.  Conversation hits have
    # {session_id, role, content}; document hits have {source, chunk_index, text}.
    # Callers that need document-specific fields (rag.py) read from this dict.
    payload: dict = field(default_factory=dict)


class VectorStore:
    """Thin async wrapper around Qdrant for storing and searching message vectors."""

    def __init__(self, url: str) -> None:
        # AsyncQdrantClient holds the connection config but does not connect
        # until the first actual call — no connection at construction time.
        self._client = AsyncQdrantClient(url=url)

    async def ensure_collection(self, name: str, dim: int) -> None:
        """Create a Qdrant collection if it does not already exist.

        Args:
            name: Collection name (e.g. "conversations").
            dim:  Vector dimension — must match the embedding model output.
                  nomic-embed-text produces 768-dimensional vectors.

        This is idempotent: calling it on a collection that already exists
        is a no-op.  Safe to call on every server startup.
        """
        try:
            exists = await self._client.collection_exists(name)
            if not exists:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(
                        size=dim,
                        # COSINE measures the angle between vectors.
                        # It is the right choice for text embeddings because
                        # the *direction* of a vector carries the meaning,
                        # not its magnitude (length).
                        distance=Distance.COSINE,
                    ),
                )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not connect to Qdrant at {settings.qdrant_url}: {exc}",
            ) from exc

    async def upsert(
        self,
        collection: str,
        vector: list[float],
        payload: dict,
        id: str | None = None,
    ) -> str:
        """Store one vector with its payload.  Returns the point ID used.

        Args:
            collection: Target collection name.
            vector:     The embedding vector (must match collection dimension).
            payload:    Arbitrary JSON dict stored alongside the vector.
                        We use {"session_id": ..., "role": ..., "content": ...}.
            id:         Optional UUID string.  A new UUID is generated if omitted.

        Returns:
            The point ID (useful if the caller wants to update the point later).
        """
        point_id = id or str(uuid4())
        try:
            await self._client.upsert(
                collection_name=collection,
                points=[PointStruct(id=point_id, vector=vector, payload=payload)],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant upsert failed: {exc}",
            ) from exc
        return point_id

    async def search(
        self,
        collection: str,
        vector: list[float],
        k: int = 5,
    ) -> list[Hit]:
        """Find the k most similar vectors to the query vector.

        Args:
            collection: Collection to search.
            vector:     The query embedding (same dimension as stored vectors).
            k:          Number of results to return.

        Returns:
            List of Hit objects ordered by score descending (most similar first).
        """
        try:
            results = await self._client.search(
                collection_name=collection,
                query_vector=vector,
                limit=k,
                with_payload=True,   # include the stored payload in results
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Qdrant search failed: {exc}",
            ) from exc

        return [
            Hit(
                score=r.score,
                session_id=r.payload.get("session_id", ""),
                role=r.payload.get("role", ""),
                content=r.payload.get("content", ""),
                payload=dict(r.payload),  # pass through the full payload for callers that need it
            )
            for r in results
        ]
