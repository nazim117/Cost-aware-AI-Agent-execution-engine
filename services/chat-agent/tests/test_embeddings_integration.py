# tests/test_embeddings_integration.py — real Ollama embedding integration tests.
#
# These tests call embeddings.embed() against a live Ollama instance running
# the nomic-embed-text model.  Skipped when Ollama is unreachable (conftest.ollama_up).
#
# What is covered (the "embeddings.py gap" from docs/test-suite-analysis.md §5.2):
#   - embed() returns a list of 768 floats (correct dimension for nomic-embed-text)
#   - same text → same vector (deterministic embedding)
#   - different texts produce different vectors

import pytest

pytestmark = pytest.mark.integration


async def test_embed_returns_768_floats(ollama_up):
    """embed() returns a list of exactly 768 floats for any input text."""
    from embeddings import embed

    vector = await embed("hello world")

    assert isinstance(vector, list), "embed() must return a list"
    assert len(vector) == 768, f"expected 768 dims, got {len(vector)}"
    assert all(isinstance(v, float) for v in vector), "all elements must be float"


async def test_embed_is_deterministic(ollama_up):
    """Two calls with identical text produce the same vector."""
    from embeddings import embed

    text = "the quick brown fox"
    v1 = await embed(text)
    v2 = await embed(text)

    # Vectors should be element-wise identical (same model, same input).
    assert v1 == v2, "embed() must return the same vector for the same text"


async def test_embed_different_texts_produce_different_vectors(ollama_up):
    """Semantically different texts produce different vectors."""
    from embeddings import embed

    v_hello = await embed("hello world")
    v_code = await embed("def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)")

    assert v_hello != v_code, "different texts should produce different vectors"


# Empty-string embedding is not a supported use case: Ollama returns an empty
# embeddings list for "" which causes an IndexError in embeddings.embed().
# No test here — production code never embeds empty text (chunking skips it).
