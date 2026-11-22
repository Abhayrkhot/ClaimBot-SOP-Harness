"""Store types shared by the SQLite and PostgreSQL backends."""

from __future__ import annotations

from dataclasses import dataclass


class ConcurrentTurnError(RuntimeError):
    """Another turn committed first; the caller should reload and retry."""


@dataclass(frozen=True)
class OutboxMessage:
    id: int
    session_id: str
    action: str
    recipient: str
    subject: str
    body: str
    status: str
    attempts: int
