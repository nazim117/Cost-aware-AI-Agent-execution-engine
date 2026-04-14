# test_vectors.py — integration tests for VectorStore.
#
# These tests call the REAL Qdrant server running on localhost:6333.
# They are marked @pytest.mark.integration:
#
#   pytest tests/test_vectors.py -v -m integration
#
# Prerequisites:
#   - Qdrant running: `docker compose up qdrant -d`
#
# Each test creates its own throwaway collection (test_<uuid>) and deletes it
# in teardown so tests are fully isolated and leave no data behind.

import math
from uuid import uuid4

import pytest
from vectors import VectorStore, Hit


QDRANT_URL = "http://localhost:6333"
DIM = 8   # Use tiny 8-dimensional vectors for speed; real tests use 768.


def make_vec(value: float, dim: int = DIM) -> list[float]:
    """Return a unit vector pointing mostly in the direction of `value`.
    Provides distinct, predictable vectors for testing without needing Ollama.
    """
    vec = [value] * dim
    # Normalise to unit length so cosine similarity works as expected.
    magnitude = math.sqrt(sum(x * x for x in vec))
    return [x / magnitude for x in vec]


@pytest.fixture
async def collection():
    """Create a fresh throwaway collection; delete it after the test."""
    name = f"test_{uuid4().hex}"
    vs = VectorStore(url=QDRANT_URL)
    await vs.ensure_collection(name, dim=DIM)
    yield vs, name
    # Teardown: delete the throwaway collection.
    await vs._client.delete_collection(name)


@pytest.mark.integration
async def test_upsert_and_search_roundtrip(collection):
    """A message upserted into Qdrant can be retrieved by a similar vector."""
    vs, col = collection

    await vs.upsert(
        collection=col,
        vector=make_vec(1.0),
        payload={"session_id": "s1", "role": "user", "content": "hello"},
    )

    hits = await vs.search(collection=col, vector=make_vec(1.0), k=1)

    assert len(hits) == 1
    assert hits[0].content == "hello"
    assert hits[0].role == "user"
    assert hits[0].session_id == "s1"
    assert hits[0].score > 0.99   # near-identical vectors → near-1.0 similarity


@pytest.mark.integration
async def test_search_returns_most_similar_first(collection):
    """When multiple vectors are stored, search returns the most similar one first."""
    vs, col = collection

    # Store two messages with very different vectors.
    await vs.upsert(col, make_vec(1.0),  {"session_id": "s1", "role": "user", "content": "cats"})
    await vs.upsert(col, make_vec(-1.0), {"session_id": "s1", "role": "user", "content": "opposite"})

    # Query with a vector close to 1.0 — "cats" should rank first.
    hits = await vs.search(col, make_vec(0.99), k=2)

    assert hits[0].content == "cats"
    assert hits[0].score > hits[1].score


@pytest.mark.integration
async def test_search_returns_at_most_k_results(collection):
    """search(k=N) returns at most N results even if more are stored."""
    vs, col = collection

    for i in range(5):
        await vs.upsert(col, make_vec(float(i + 1)), {"session_id": "s1", "role": "user", "content": f"msg{i}"})

    hits = await vs.search(col, make_vec(1.0), k=3)
    assert len(hits) <= 3


@pytest.mark.integration
async def test_ensure_collection_is_idempotent(collection):
    """Calling ensure_collection twice on the same name does not raise."""
    vs, col = collection
    # Already created by the fixture — calling again should be a no-op.
    await vs.ensure_collection(col, dim=DIM)


@pytest.mark.integration
async def test_hit_fields_are_populated(collection):
    """All Hit fields are populated from the stored payload."""
    vs, col = collection

    await vs.upsert(
        col,
        make_vec(1.0),
        {"session_id": "sess-abc", "role": "assistant", "content": "I am the assistant."},
    )

    hits = await vs.search(col, make_vec(1.0), k=1)
    h = hits[0]

    assert h.session_id == "sess-abc"
    assert h.role == "assistant"
    assert h.content == "I am the assistant."
    assert isinstance(h.score, float)
