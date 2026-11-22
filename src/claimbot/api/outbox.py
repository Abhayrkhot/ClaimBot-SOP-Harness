"""Transactional outbox dispatcher: delivers committed summary emails over SMTP, at most once.

The turn that authorizes an email only inserts an outbox row in the same transaction as the
session update. This worker delivers pending rows and marks them sent, so a crash between the
turn and delivery loses nothing and a retried turn cannot produce a second email.
"""

from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage
from typing import Any

from ..config import Settings
from ..obs import metrics

LOG = logging.getLogger("claimbot.outbox")


class OutboxDispatcher:
    def __init__(self, store: Any, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.last_batch = 0

    BATCH = 50

    async def deliver_once(self) -> int:
        """Deliver one batch. Returns how many messages were attempted (sent or failed)."""
        import aiosmtplib

        sent = 0
        batch = await self.store.outbox_pending(self.BATCH)
        self.last_batch = len(batch)
        for message in batch:
            email = EmailMessage()
            email["From"] = self.settings.smtp_sender
            email["To"] = message.recipient
            email["Subject"] = message.subject
            email.set_content(message.body)
            try:
                await aiosmtplib.send(
                    email, hostname=self.settings.smtp_host, port=self.settings.smtp_port, timeout=10
                )
            except (aiosmtplib.SMTPException, OSError) as exc:
                LOG.warning(
                    "outbox delivery failed", extra={"outbox_id": message.id, "error": type(exc).__name__}
                )
                metrics.OUTBOX.labels(outcome="error").inc()
                await self.store.mark_outbox(message.id, delivered=False)
                continue
            await self.store.mark_outbox(message.id, delivered=True)
            metrics.OUTBOX.labels(outcome="sent").inc()
            sent += 1
        return sent

    async def run(self) -> None:
        """Poll for committed summaries; keep draining without sleeping while batches come back full."""
        while True:
            self.last_batch = 0
            try:
                await self.deliver_once()
            except Exception:
                LOG.exception("outbox loop error")
            if self.last_batch < self.BATCH:
                await asyncio.sleep(self.settings.outbox_poll_seconds)
