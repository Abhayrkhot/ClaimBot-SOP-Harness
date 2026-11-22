"""SQLite session store with optimistic concurrency, an audit log and a transactional outbox.

A commit writes the session row (guarded by its revision), the turn's audit events and any
authorized side effects in one transaction. Two requests that loaded the same revision cannot
both commit: the second gets ``ConcurrentTurnError`` and the API returns 409. The outbox has a
unique key per (session, action), so a retried send can never queue a second email.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, ClassVar

import aiosqlite

from ..sop.state import ActionRequest, AuditEvent
from .base import ConcurrentTurnError, OutboxMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  revision INTEGER NOT NULL,
  expires_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  turn INTEGER NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_session ON audit_events(session_id, id);
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  action TEXT NOT NULL,
  recipient TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  sent_at REAL,
  UNIQUE(session_id, action)
);
"""


class SQLiteStore:
    def __init__(self, path: str = ":memory:", ttl_seconds: int = 1800) -> None:
        self.path = path
        self.ttl = ttl_seconds
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> SQLiteStore:
        self._db = await aiosqlite.connect(self.path, timeout=15)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        return self

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("store is not open")
        return self._db

    async def ping(self) -> bool:
        async with self.db.execute("SELECT 1") as cur:
            return (await cur.fetchone()) is not None

    async def load(self, session_id: str) -> tuple[str, int] | None:
        async with (
            self._lock,
            self.db.execute(
                "SELECT state, revision FROM sessions WHERE id=? AND expires_at>=?", (session_id, time.time())
            ) as cur,
        ):
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
        now = time.time()
        async with self._lock:
            db = self.db
            await db.execute("BEGIN IMMEDIATE")
            try:
                if expected_revision == 0:
                    cur = await db.execute(
                        "INSERT OR IGNORE INTO sessions(id, state, revision, expires_at, updated_at)"
                        " VALUES (?, ?, 1, ?, ?)",
                        (session_id, state_json, now + self.ttl, now),
                    )
                else:
                    cur = await db.execute(
                        "UPDATE sessions SET state=?, revision=revision+1, expires_at=?, updated_at=?"
                        " WHERE id=? AND revision=?",
                        (state_json, now + self.ttl, now, session_id, expected_revision),
                    )
                if cur.rowcount != 1:
                    raise ConcurrentTurnError(session_id)
                await db.executemany(
                    "INSERT INTO audit_events(session_id, turn, ts, kind, data) VALUES (?, ?, ?, ?, ?)",
                    [(session_id, turn, now, e.kind, json.dumps(e.data, default=str)) for e in events],
                )
                for a in actions:
                    await db.execute(
                        "INSERT OR IGNORE INTO outbox(session_id, action, recipient, subject, body, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (session_id, a.action, a.recipient, a.subject, a.body, now),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return expected_revision + 1

    async def purge_expired(self) -> int:
        async with self._lock:
            cur = await self.db.execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
            await self.db.commit()
            return cur.rowcount

    async def audit(self, session_id: str) -> list[dict[str, Any]]:
        async with (
            self._lock,
            self.db.execute(
                "SELECT turn, ts, kind, data FROM audit_events WHERE session_id=? ORDER BY id", (session_id,)
            ) as cur,
        ):
            rows = await cur.fetchall()
        return [{"turn": r[0], "ts": r[1], "kind": r[2], "data": json.loads(r[3])} for r in rows]

    _OUTBOX_COLUMNS = "SELECT id, session_id, action, recipient, subject, body, status, attempts FROM outbox"
    _OUTBOX_QUERIES: ClassVar[dict[str, str]] = {
        "session": _OUTBOX_COLUMNS + " WHERE session_id=?",
        "pending": _OUTBOX_COLUMNS + " WHERE status='pending' AND attempts<5 ORDER BY id LIMIT ?",
        "recent": _OUTBOX_COLUMNS + " ORDER BY id DESC LIMIT ?",
    }

    async def outbox_for(self, session_id: str) -> list[OutboxMessage]:
        return await self._outbox("session", (session_id,))

    async def outbox_pending(self, limit: int = 20) -> list[OutboxMessage]:
        return await self._outbox("pending", (limit,))

    async def outbox_all(self, limit: int = 100) -> list[OutboxMessage]:
        return await self._outbox("recent", (limit,))

    async def _outbox(self, name: str, params: tuple[Any, ...]) -> list[OutboxMessage]:
        async with self._lock, self.db.execute(self._OUTBOX_QUERIES[name], params) as cur:
            rows = await cur.fetchall()
        return [OutboxMessage(*row) for row in rows]

    async def mark_outbox(self, message_id: int, delivered: bool) -> None:
        async with self._lock:
            if delivered:
                await self.db.execute(
                    "UPDATE outbox SET status='sent', attempts=attempts+1, sent_at=? WHERE id=?",
                    (time.time(), message_id),
                )
            else:
                await self.db.execute(
                    "UPDATE outbox SET attempts=attempts+1,"
                    " status=CASE WHEN attempts+1>=5 THEN 'failed' ELSE 'pending' END WHERE id=?",
                    (message_id,),
                )
            await self.db.commit()
