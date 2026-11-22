"""Store contract tests, run against SQLite always and PostgreSQL when DATABASE_URL is set."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from claimbot.sop.state import ActionRequest, AuditEvent
from claimbot.store.base import ConcurrentTurnError
from claimbot.store.sqlite import SQLiteStore

PG_URL = os.getenv("DATABASE_URL")
BACKENDS = ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]


async def open_backend(name: str, ttl: int = 1800) -> Any:
    if name == "sqlite":
        return await SQLiteStore(":memory:", ttl).open()
    if not PG_URL:
        pytest.skip("DATABASE_URL not set")
    from claimbot.store.postgres import PostgresStore

    store = await PostgresStore(PG_URL, ttl).open()
    async with store.pool.connection() as conn:
        await conn.execute("TRUNCATE sessions, audit_events, outbox RESTART IDENTITY")
    return store


@pytest.fixture(params=BACKENDS)
async def store(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    s = await open_backend(request.param)
    yield s
    await s.close()


async def test_optimistic_concurrency(store: Any) -> None:
    assert await store.commit("s1", 0, "{}", 1, [], []) == 1
    with pytest.raises(ConcurrentTurnError):
        await store.commit("s1", 0, "{}", 1, [], [])  # second create of the same id
    assert await store.commit("s1", 1, '{"a":1}', 2, [], []) == 2
    with pytest.raises(ConcurrentTurnError):
        await store.commit("s1", 1, '{"a":2}', 2, [], [])  # stale revision
    state, revision = await store.load("s1")
    assert revision == 2 and state.replace(" ", "") == '{"a":1}'


async def test_outbox_is_idempotent_per_session_action(store: Any) -> None:
    a = ActionRequest("email_summary", "x@example.com", "s", "b")
    await store.commit("s1", 0, "{}", 1, [], [a])
    await store.commit("s1", 1, "{}", 2, [], [a])
    assert len(await store.outbox_for("s1")) == 1
    pending = await store.outbox_pending()
    await store.mark_outbox(pending[0].id, delivered=True)
    assert await store.outbox_pending() == []


async def test_failed_commit_rolls_back_events(store: Any) -> None:
    await store.commit("s1", 0, "{}", 1, [AuditEvent("a")], [])
    with pytest.raises(ConcurrentTurnError):
        await store.commit("s1", 0, "{}", 1, [AuditEvent("b")], [])
    assert [e["kind"] for e in await store.audit("s1")] == ["a"]


async def test_concurrent_commits_one_wins(store: Any) -> None:
    await store.commit("s1", 0, "{}", 1, [], [])
    results = await asyncio.gather(
        *(store.commit("s1", 1, f'{{"n":{i}}}', 2, [], []) for i in range(8)), return_exceptions=True
    )
    assert sum(1 for r in results if r == 2) == 1
    assert sum(1 for r in results if isinstance(r, ConcurrentTurnError)) == 7


@pytest.mark.parametrize("backend", BACKENDS)
async def test_expired_sessions_are_invisible(backend: str) -> None:
    s = await open_backend(backend, ttl=-1)
    await s.commit("s1", 0, "{}", 1, [], [])
    assert await s.load("s1") is None
    assert await s.purge_expired() == 1
    await s.close()


async def test_outbox_claims_are_exclusive(store: Any) -> None:
    for i in range(6):
        await store.commit(
            f"s{i}", 0, "{}", 1, [], [ActionRequest("email_summary", "x@example.com", "s", "b")]
        )
    batches = await asyncio.gather(*(store.outbox_pending(limit=6) for _ in range(3)))
    ids = [m.id for batch in batches for m in batch]
    if isinstance(store, SQLiteStore):
        return  # SQLite runs one dispatcher; only PostgreSQL promises exclusive claims
    assert sorted(ids) == sorted(set(ids)) and len(ids) == 6


@pytest.mark.postgres
async def test_concurrent_startup_on_empty_database() -> None:
    """Four workers creating the schema at once must not race in the catalog."""
    if not PG_URL:
        pytest.skip("DATABASE_URL not set")
    from claimbot.store.postgres import PostgresStore

    first = await PostgresStore(PG_URL).open()
    async with first.pool.connection() as conn:
        await conn.execute("DROP TABLE IF EXISTS sessions, audit_events, outbox")
    await first.close()
    stores = await asyncio.gather(*(PostgresStore(PG_URL).open() for _ in range(4)))
    assert all([await s.ping() for s in stores])
    for s in stores:
        await s.close()
