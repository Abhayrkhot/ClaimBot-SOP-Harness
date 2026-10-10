"""Transactional SQLite persistence for conversations and demo email previews.

One process-level lock and ``BEGIN IMMEDIATE`` serialize state-changing turns,
which prevents duplicate consent actions and lost updates in this compact demo.
Sessions and outbox rows expire together; callers can read only their own queued
preview through the chat response.
"""
import sqlite3
import time
from contextlib import contextmanager
from threading import RLock

from .models import Session


class Store:
    """Persist typed sessions and an idempotent, simulated email outbox."""
    def __init__(self, path=':memory:', ttl=1800):
        self.ttl = ttl
        self.lock = RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL, expires REAL NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS outbox (session_id TEXT PRIMARY KEY, recipient TEXT, subject TEXT, body TEXT, expires REAL NOT NULL)')
        self.db.commit()

    @contextmanager
    def turn(self):
        """Open one atomic workflow turn and roll it back on any exception."""
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                now = time.time()
                self.db.execute('DELETE FROM sessions WHERE expires < ?', (now,))
                self.db.execute('DELETE FROM outbox WHERE expires < ?', (now,))
                yield
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def get(self, sid):
        row = self.db.execute('SELECT state FROM sessions WHERE id=? AND expires>=?', (sid, time.time())).fetchone()
        return Session.model_validate_json(row[0]) if row else None

    def save(self, sid, state):
        self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)',
                        (sid, state.model_dump_json(), time.time() + self.ttl))

    def queue(self, sid, recipient, body):
        """Queue at most one summary per session after workflow authorization."""
        self.db.execute('INSERT OR IGNORE INTO outbox VALUES (?, ?, ?, ?, ?)',
                        (sid, recipient, 'Insurance claim conversation summary', body, time.time() + self.ttl))

    OUTBOX_COLUMNS = ('to', 'subject', 'body')
    _OUTBOX_SELECT = 'SELECT recipient, subject, body FROM outbox WHERE expires>=?'

    def _outbox_rows(self, extra_clause='', params=()):
        with self.lock:
            rows = self.db.execute(self._OUTBOX_SELECT + extra_clause, (time.time(), *params))
            return [dict(zip(self.OUTBOX_COLUMNS, row, strict=True)) for row in rows]

    def message_for(self, sid):
        """The queued summary for one session, so the caller can read what was sent."""
        rows = self._outbox_rows(' AND session_id=?', (sid,))
        return rows[0] if rows else None

    def messages(self):
        return self._outbox_rows()
