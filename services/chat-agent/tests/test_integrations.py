# test_integrations.py — unit tests for SyncStore.
#
# The Jira/GitHub adapter tests that used to live here have been removed:
# those adapters no longer exist.  The fetch logic is now inlined in sync.py
# and tested via the sync_project tests in test_sync.py.
#
# SyncStore tests use a fresh SQLite temp file per test (tmp_path fixture).

import pytest

from sync import SyncStore


async def test_sync_store_last_synced_none_initially(tmp_path):
    """A fresh store returns None for last_synced_at."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    assert await store.get_last_synced("proj-1", "jira_project_key", "ALPHA") is None


async def test_sync_store_set_and_get(tmp_path):
    """set_last_synced / get_last_synced round-trip."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    ts = "2024-06-01T12:00:00+00:00"
    await store.set_last_synced("proj-1", "jira_project_key", "ALPHA", ts)
    assert await store.get_last_synced("proj-1", "jira_project_key", "ALPHA") == ts


async def test_sync_store_set_overwrites(tmp_path):
    """Calling set_last_synced twice updates the timestamp."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    await store.set_last_synced("proj-1", "github_repo", "org/repo", "2024-01-01T00:00:00Z")
    ts2 = "2024-06-01T00:00:00Z"
    await store.set_last_synced("proj-1", "github_repo", "org/repo", ts2)
    assert await store.get_last_synced("proj-1", "github_repo", "org/repo") == ts2


async def test_sync_store_record_action_does_not_raise(tmp_path):
    """record_action writes a row without error."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    await store.record_action("proj-1", "sync", {"items": 5, "chunks": 10})


async def test_sync_store_get_sync_status_empty(tmp_path):
    """get_sync_status returns [] for a project that has never been synced."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    assert await store.get_sync_status("proj-1") == []


async def test_sync_store_delete_by_project(tmp_path):
    """delete_by_project removes only the target project's rows."""
    store = SyncStore(str(tmp_path / "test.db"))
    await store.init()
    await store.set_last_synced("proj-1", "github_repo", "org/repo", "2024-01-01T00:00:00Z")
    await store.set_last_synced("proj-2", "github_repo", "org/repo", "2024-01-01T00:00:00Z")
    await store.delete_by_project("proj-1")
    assert await store.get_last_synced("proj-1", "github_repo", "org/repo") is None
    assert await store.get_last_synced("proj-2", "github_repo", "org/repo") is not None


pytestmark = pytest.mark.asyncio
