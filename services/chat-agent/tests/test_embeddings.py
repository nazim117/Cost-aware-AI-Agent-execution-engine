# test_embeddings.py — integration tests for the embed() function.
#
# These tests call the REAL Ollama server running on localhost:11434.
# They are marked @pytest.mark.integration so you can run them separately:
#
#   pytest tests/test_embeddings.py -v -m integration
#
# Prerequisites:
#   - Ollama installed and running: `ollama serve`
#   - nomic-embed-text pulled: `ollama pull nomic-embed-text`
#
# Why test embeddings at all?
#   embed() has two failure modes that unit tests can't catch:
#   1. The Ollama client API changes (e.g. .embed() vs .embeddings()).
#   2. The model returns a wrong-shaped vector.
#   These integration tests catch both.

import math
import pytest
from embeddings import embed


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.
    Used to verify that similar texts produce similar embeddings.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@pytest.mark.integration
async def test_embed_returns_correct_dimension():
    """nomic-embed-text always produces 768-dimensional vectors."""
    vec = await embed("hello world")
    assert len(vec) == 768, f"Expected 768 dimensions, got {len(vec)}"


@pytest.mark.integration
async def test_embed_returns_floats():
    """Each element of the vector should be a float."""
    vec = await embed("test sentence")
    assert all(isinstance(x, float) for x in vec)


@pytest.mark.integration
@pytest.mark.parametrize("text_a,text_b,min_similarity", [
    # Semantically similar sentences should produce close vectors.
    # nomic-embed-text scores paraphrased sentences around 0.75; using 0.70
    # as the floor so the test is stable without being trivially loose.
    (
        "The dog ran quickly through the park.",
        "A puppy sprinted fast across the field.",
        0.70,
    ),
    # The same text should produce an identical (or near-identical) vector.
    (
        "What is the capital of France?",
        "What is the capital of France?",
        0.999,
    ),
])
async def test_similar_texts_have_high_cosine_similarity(text_a, text_b, min_similarity):
    """Two semantically similar sentences should be close in vector space."""
    vec_a = await embed(text_a)
    vec_b = await embed(text_b)
    similarity = cosine_similarity(vec_a, vec_b)
    assert similarity >= min_similarity, (
        f"Expected similarity >= {min_similarity}, got {similarity:.3f}\n"
        f"  text_a: {text_a}\n"
        f"  text_b: {text_b}"
    )


@pytest.mark.integration
async def test_unrelated_texts_have_low_cosine_similarity():
    """Two unrelated texts should be far apart in vector space."""
    vec_a = await embed("The dog ran quickly through the park.")
    vec_b = await embed("Quarterly earnings report for fiscal year 2024.")
    similarity = cosine_similarity(vec_a, vec_b)
    assert similarity < 0.6, (
        f"Expected similarity < 0.6 for unrelated texts, got {similarity:.3f}"
    )
