"""PostgreSQL session store: same contract as the SQLite store, safe across many API replicas.

Concurrency is optimistic (``UPDATE ... WHERE revision = $expected``), so replicas need no
shared lock and a stale writer gets ``ConcurrentTurnError``. Outbox rows are claimed with
``FOR UPDATE SKIP LOCKED`` so any number of dispatchers can run without double delivery, and a
claim that is not confirmed within five minutes (a crashed dispatcher) becomes claimable again.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import tuple_row
from psycopg_pool import AsyncConnectionPool

from ..sop.state import ActionRequest, AuditEvent
from .base import ConcurrentTurnError, OutboxMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  state JSONB NOT NULL,
  revision INTEGER NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS audit_events (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn INTEGER NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind TEXT NOT NULL,
  data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_session ON audit_events(session_id, id);
CREATE TABLE IF NOT EXISTS outbox (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  action TEXT NOT NULL,
  recipient TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  claimed_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  UNIQUE (session_id, action)
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(id) WHERE status IN ('pending', 'sending');
"""

_COLUMNS = "id, session_id, action, recipient, subject, body, status, attempts"


class PostgresStore:
    def __init__(self, url: str, ttl_seconds: int = 1800, max_size: int = 20) -> None:
        self.url = url
        self.ttl = ttl_seconds
        self.pool = AsyncConnectionPool(
            url, min_size=1, max_size=max_size, open=False, kwargs={"autocommit": True}
        )

    async def open(self) -> PostgresStore:
        await self.pool.open(wait=True, timeout=30)
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)
        return self

    async def close(self) -> None:
        await self.pool.close()

    async def ping(self) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1")
            return (await cur.fetchone()) is not None

    async def load(self, session_id: str) -> tuple[str, int] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT state::text, revision FROM sessions WHERE id=%s AND expires_at >= now()",
                (session_id,),
            )
            row = await cur.fetchone()
        return (row[0], int(row[1])) if row else None

    async def commit(
        self,
        session_id: str,
        expected_revision: int,
        state_json: str,
        turn: int,
        events: list[AuditEvent],
        actions: list[ActionRequest],
    ) -> int:
        ttl = f"{self.ttl} seconds"
        async with self.pool.connection() as conn, conn.transaction():
            if expected_revision == 0:
                cur = await conn.execute(
                    "INSERT INTO sessions(id, state, revision, expires_at) VALUES (%s, %s::jsonb, 1,"
                    " now() + %s::interval) ON CONFLICT (id) DO NOTHING",
                    (session_id, state_json, ttl),
                )
            else:
                cur = await conn.execute(
                    "UPDATE sessions SET state=%s::jsonb, revision=revision+1, expires_at=now() + %s::interval,"
                    " updated_at=now() WHERE id=%s AND revision=%s",
                    (state_json, ttl, session_id, expected_revision),
                )
            if cur.rowcount != 1:
                raise ConcurrentTurnError(session_id)
            if events:
                async with conn.cursor() as c:
                    await c.executemany(
                        "INSERT INTO audit_events(session_id, turn, kind, data) VALUES (%s, %s, %s, %s::jsonb)",
                        [(session_id, turn, e.kind, json.dumps(e.data, default=str)) for e in events],
                    )
            for a in actions:
                await conn.execute(
                    "INSERT INTO outbox(session_id, action, recipient, subject, body) VALUES (%s, %s, %s, %s, %s)"
                    " ON CONFLICT (session_id, action) DO NOTHING",
                    (session_id, a.action, a.recipient, a.subject, a.body),
                )
        return expected_revision + 1

    async def purge_expired(self) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute("DELETE FROM sessions WHERE expires_at < now()")
            return cur.rowcount

    async def audit(self, session_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT turn, extract(epoch FROM ts), kind, data FROM audit_events WHERE session_id=%s ORDER BY id",
                (session_id,),
            )
            rows = await cur.fetchall()
        return [{"turn": r[0], "ts": float(r[1]), "kind": r[2], "data": r[3]} for r in rows]

    async def _rows(self, query: str, params: tuple[Any, ...]) -> list[OutboxMessage]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=tuple_row) as cur:
            await cur.execute(query, params)
            rows = await cur.fetchall()
        return [OutboxMessage(*row) for row in rows]

    async def outbox_for(self, session_id: str) -> list[OutboxMessage]:
        return await self._rows(f"SELECT {_COLUMNS} FROM outbox WHERE session_id=%s", (session_id,))  # noqa: S608

    async def outbox_all(self, limit: int = 100) -> list[OutboxMessage]:
        return await self._rows(f"SELECT {_COLUMNS} FROM outbox ORDER BY id DESC LIMIT %s", (limit,))  # noqa: S608

    async def outbox_pending(self, limit: int = 20) -> list[OutboxMessage]:
        """Claim deliverable rows; concurrent dispatchers never receive the same row."""
        return await self._rows(
            f"UPDATE outbox SET status='sending', claimed_at=now(), attempts=attempts+1"  # noqa: S608
            f" WHERE id IN (SELECT id FROM outbox WHERE attempts < 5 AND (status='pending'"
            f" OR (status='sending' AND claimed_at < now() - interval '5 minutes'))"
            f" ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING {_COLUMNS}",
            (limit,),
        )

    async def mark_outbox(self, message_id: int, delivered: bool) -> None:
        async with self.pool.connection() as conn:
            if delivered:
                await conn.execute(
                    "UPDATE outbox SET status='sent', sent_at=now() WHERE id=%s", (message_id,)
                )
            else:
                await conn.execute(
                    "UPDATE outbox SET status=CASE WHEN attempts >= 5 THEN 'failed' ELSE 'pending' END WHERE id=%s",
                    (message_id,),
                )
