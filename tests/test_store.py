from __future__ import annotations

import asyncio

import pytest

from claimbot.sop.state import ActionRequest, AuditEvent
from claimbot.store.base import ConcurrentTurnError
from claimbot.store.sqlite import SQLiteStore


async def test_optimistic_concurrency(store: SQLiteStore) -> None:
    assert await store.commit("s1", 0, "{}", 1, [], []) == 1
    with pytest.raises(ConcurrentTurnError):
        await store.commit("s1", 0, "{}", 1, [], [])  # second create of the same id
    assert await store.commit("s1", 1, '{"a":1}', 2, [], []) == 2
    with pytest.raises(ConcurrentTurnError):
        await store.commit("s1", 1, '{"a":2}', 2, [], [])  # stale revision
    assert await store.load("s1") == ('{"a":1}', 2)


async def test_outbox_is_idempotent_per_session_action(store: SQLiteStore) -> None:
    a = ActionRequest("email_summary", "x@example.com", "s", "b")
    await store.commit("s1", 0, "{}", 1, [], [a])
    await store.commit("s1", 1, "{}", 2, [], [a])
    assert len(await store.outbox_for("s1")) == 1
    pending = await store.outbox_pending()
    await store.mark_outbox(pending[0].id, delivered=True)
    assert await store.outbox_pending() == []


async def test_failed_commit_rolls_back_events(store: SQLiteStore) -> None:
    await store.commit("s1", 0, "{}", 1, [AuditEvent("a")], [])
    with pytest.raises(ConcurrentTurnError):
        await store.commit("s1", 0, "{}", 1, [AuditEvent("b")], [])
    assert [e["kind"] for e in await store.audit("s1")] == ["a"]


async def test_concurrent_commits_one_wins(store: SQLiteStore) -> None:
    await store.commit("s1", 0, "{}", 1, [], [])
    results = await asyncio.gather(
        *(store.commit("s1", 1, f'{{"n":{i}}}', 2, [], []) for i in range(8)), return_exceptions=True
    )
    assert sum(1 for r in results if r == 2) == 1
    assert sum(1 for r in results if isinstance(r, ConcurrentTurnError)) == 7


async def test_expired_sessions_are_invisible() -> None:
    s = await SQLiteStore(":memory:", ttl_seconds=-1).open()
    await s.commit("s1", 0, "{}", 1, [], [])
    assert await s.load("s1") is None
    assert await s.purge_expired() == 1
    await s.close()
