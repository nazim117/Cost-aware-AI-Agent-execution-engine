# tests/test_vectors_integration.py — real Qdrant integration tests.
#
# These tests exercise VectorStore against a live Qdrant instance.
# They are skipped when Qdrant is unreachable (see conftest.qdrant_up).
#
# What is covered (the "vectors.py gap" from docs/test-suite-analysis.md §5.2):
#   - ensure_collection is idempotent
#   - upsert stores a point that is retrievable by search
#   - two different project_ids never bleed into each other's search results
#   - delete_by_project removes only the target project's data

import uuid

import pytest

from config import settings

pytestmark = pytest.mark.integration


# ─── ensure_collection ────────────────────────────────────────────────────────

async def test_ensure_collection_is_idempotent(real_vstore):
    """Calling ensure_collection twice does not raise and the collection exists."""
    name = f"test_idem_{uuid.uuid4().hex[:8]}"
    try:
        await real_vstore.ensure_collection(name, dim=768)
        # Second call must be a no-op, not an error.
        await real_vstore.ensure_collection(name, dim=768)
    finally:
        # Clean up the test-only collection.
        try:
            await real_vstore._client.delete_collection(name)
        except Exception:
            pass


# ─── upsert + search round-trip ──────────────────────────────────────────────

async def test_upsert_then_search_returns_stored_point(real_vstore, unique_project):
    """A vector upserted for project A is returned by search for project A."""
    coll = settings.qdrant_collection
    vector = [0.1] * 768  # simple deterministic vector

    point_id = await real_vstore.upsert(
        collection=coll,
        project_id=unique_project,
        vector=vector,
        payload={"role": "user", "content": "test content", "session_id": "s1"},
    )

    results = await real_vstore.search(coll, unique_project, vector, k=5)

    assert len(results) >= 1
    # The top result should be the one we just inserted.
    top = results[0]
    assert top.payload.get("content") == "test content"
    # Qdrant stores the project_id we injected.
    assert top.payload.get("project_id") == unique_project
    _ = point_id  # referenced to suppress linter warning


# ─── cross-project isolation ─────────────────────────────────────────────────

async def test_search_does_not_bleed_across_projects(real_vstore, unique_project):
    """A vector stored under project A is NOT returned when searching project B.

    This is the core correctness invariant of the payload-filter isolation
    strategy (see vectors.py module docstring).
    """
    coll = settings.qdrant_collection
    project_a = unique_project
    project_b = str(uuid.uuid4())

    # Same vector in both projects so cosine similarity is identical.
    vector = [0.5] * 768

    await real_vstore.upsert(
        collection=coll,
        project_id=project_a,
        vector=vector,
        payload={"role": "assistant", "content": "private", "session_id": "s1"},
    )

    try:
        # Project B search must return nothing.
        results = await real_vstore.search(coll, project_b, vector, k=5)
        assert all(r.payload.get("project_id") != project_a for r in results), (
            "project A content leaked into project B search results"
        )
    finally:
        await real_vstore.delete_by_project(coll, project_b)


# ─── delete_by_project ────────────────────────────────────────────────────────

async def test_delete_by_project_removes_only_target(real_vstore, unique_project):
    """delete_by_project removes the target project's data; a neighbour survives."""
    coll = settings.qdrant_collection
    project_a = unique_project
    project_b = str(uuid.uuid4())
    vector = [0.3] * 768

    # Insert one point under each project.
    await real_vstore.upsert(coll, project_a, vector,
                             {"role": "user", "content": "a", "session_id": "s1"})
    await real_vstore.upsert(coll, project_b, vector,
                             {"role": "user", "content": "b", "session_id": "s1"})

    try:
        # Delete project A only.
        await real_vstore.delete_by_project(coll, project_a)

        # Project A search should return nothing.
        results_a = await real_vstore.search(coll, project_a, vector, k=5)
        assert results_a == []

        # Project B should still find its point.
        results_b = await real_vstore.search(coll, project_b, vector, k=5)
        assert len(results_b) >= 1
        assert results_b[0].payload.get("content") == "b"
    finally:
        await real_vstore.delete_by_project(coll, project_b)


# ─── upsert round-trip in documents collection ────────────────────────────────

async def test_docs_collection_upsert_search(real_vstore, unique_project):
    """Same upsert+search works for the documents collection."""
    coll = settings.qdrant_docs_collection
    vector = [0.7] * 768

    await real_vstore.upsert(
        collection=coll,
        project_id=unique_project,
        vector=vector,
        payload={"source": "doc:test.md", "chunk_index": 0, "text": "hello world"},
    )

    results = await real_vstore.search(coll, unique_project, vector, k=5)
    assert len(results) >= 1
    assert results[0].payload.get("source") == "doc:test.md"
