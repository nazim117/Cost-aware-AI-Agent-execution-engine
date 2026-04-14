# test_rag.py — integration + unit tests for the RAG pipeline.
#
# Integration tests call the REAL Ollama + Qdrant servers.
# Run them with:
#   pytest tests/test_rag.py -v -m integration
#
# Prerequisites:
#   - Ollama running with nomic-embed-text pulled
#   - Qdrant running: `docker compose up qdrant -d`
#
# Each integration test creates a throwaway Qdrant collection (deleted in
# teardown) so tests are fully isolated and leave no state behind.

from pathlib import Path
from uuid import uuid4

import pytest

from rag import Chunk, chunk_text, ingest, retrieve
from vectors import VectorStore


QDRANT_URL = "http://localhost:6333"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Unit tests — no external services needed
# ---------------------------------------------------------------------------

def test_chunk_text_single_chunk_for_short_text():
    """Text shorter than `size` produces exactly one chunk."""
    result = chunk_text("hello world", size=500, overlap=50)
    assert result == ["hello world"]


def test_chunk_text_correct_number_of_chunks():
    """A 1000-char text with size=500, overlap=50 should produce 3 chunks.
    step = 500 - 50 = 450
    chunk 0: [0, 500)
    chunk 1: [450, 950)
    chunk 2: [900, 1000)  (shorter last chunk)
    """
    text = "x" * 1000
    chunks = chunk_text(text, size=500, overlap=50)
    assert len(chunks) == 3


def test_chunk_text_overlap_is_present():
    """Adjacent chunks share `overlap` characters at their boundary."""
    text = "abcdefghij"   # 10 chars
    chunks = chunk_text(text, size=6, overlap=2)
    # chunk 0: "abcdef"
    # chunk 1: "efghij"  (shares "ef" with chunk 0)
    assert chunks[0][-2:] == chunks[1][:2]


def test_chunk_text_last_chunk_included():
    """The final characters of the text always appear in the last chunk."""
    text = "abcdefghijklmnop"   # 16 chars
    chunks = chunk_text(text, size=6, overlap=2)
    # The last chunk must end with the last characters of the input.
    assert chunks[-1] == text[len(text) - len(chunks[-1]):]
    assert text.endswith(chunks[-1])


def test_chunk_text_empty_string():
    """Empty input returns an empty list."""
    assert chunk_text("") == []


# ---------------------------------------------------------------------------
# Integration test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def col():
    """Create a throwaway Qdrant collection; delete it after the test."""
    name = f"test_rag_{uuid4().hex}"
    vs = VectorStore(url=QDRANT_URL)
    await vs.ensure_collection(name, dim=768)
    yield vs, name
    await vs._client.delete_collection(name)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
async def test_ingest_returns_correct_chunk_count(col, monkeypatch):
    """ingest() should return the number of chunks produced by chunk_text()."""
    vs, col_name = col

    # Patch qdrant_docs_collection to use our throwaway collection.
    import config as cfg
    monkeypatch.setattr(cfg.settings, "qdrant_docs_collection", col_name)

    text = "x" * 1000   # 1000 chars → chunk_text(size=500, overlap=50) → 3 chunks
    count = await ingest("test-source", text, vs)
    assert count == 3


@pytest.mark.integration
async def test_retrieve_finds_relevant_chunk(col, monkeypatch):
    """After ingesting the fixture file, a relevant query returns a matching chunk."""
    vs, col_name = col

    import config as cfg
    monkeypatch.setattr(cfg.settings, "qdrant_docs_collection", col_name)

    fixture_text = (FIXTURES_DIR / "sample.md").read_text(encoding="utf-8")
    await ingest("sample.md", fixture_text, vs)

    # Query with a phrase only present in the fixture — "Bluebell DB" and
    # "write-ahead log" are specific enough that the top hit should be the
    # WAL-related chunk.
    chunks = await retrieve("write-ahead log durability", k=3, vstore=vs)

    assert len(chunks) > 0
    assert chunks[0].source == "sample.md"
    # The top chunk should contain WAL-related content.
    assert any(
        "WAL" in c.text or "write-ahead" in c.text.lower() or "log" in c.text
        for c in chunks
    )


@pytest.mark.integration
async def test_retrieve_ranks_relevant_above_irrelevant(col, monkeypatch):
    """A query for topic A should rank topic-A chunks above topic-B chunks."""
    vs, col_name = col

    import config as cfg
    monkeypatch.setattr(cfg.settings, "qdrant_docs_collection", col_name)

    # Two completely different topics.
    await ingest(
        "databases.txt",
        "Bluebell DB uses a write-ahead log for durability and bloom filters "
        "for fast key lookups on disk.",
        vs,
    )
    await ingest(
        "cooking.txt",
        "To make a perfect omelette, whisk three eggs with salt and cook on "
        "medium heat. Fold gently before serving.",
        vs,
    )

    chunks = await retrieve("write-ahead log database storage", k=2, vstore=vs)

    # The database chunk should be the top hit.
    assert chunks[0].source == "databases.txt"
    assert chunks[0].score > chunks[1].score


@pytest.mark.integration
async def test_ingest_stores_source_and_chunk_index(col, monkeypatch):
    """Each stored chunk should have the correct source and chunk_index in its payload."""
    vs, col_name = col

    import config as cfg
    monkeypatch.setattr(cfg.settings, "qdrant_docs_collection", col_name)

    # Short text → one chunk (chunk_index 0).
    await ingest("my-file.txt", "hello world this is a test document", vs)

    chunks = await retrieve("hello world", k=1, vstore=vs)

    assert len(chunks) == 1
    assert chunks[0].source == "my-file.txt"
    assert chunks[0].chunk_index == 0
    assert "hello" in chunks[0].text
