"""Outbox delivery, structured logging and tracing setup."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from claimbot.api.outbox import OutboxDispatcher
from claimbot.config import Settings
from claimbot.obs.logging import JsonFormatter, request_id_var, scrub
from claimbot.sop.state import ActionRequest
from claimbot.store.sqlite import SQLiteStore


async def test_outbox_delivers_and_retries(store: SQLiteStore, monkeypatch: pytest.MonkeyPatch) -> None:
    import aiosmtplib

    sent: list[Any] = []
    failures = {"n": 1}

    async def fake_send(message: Any, **kwargs: Any) -> None:
        if failures["n"]:
            failures["n"] -= 1
            raise aiosmtplib.SMTPConnectError("down")
        sent.append((message["To"], kwargs["hostname"], kwargs["port"]))

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await store.commit(
        "s1", 0, "{}", 1, [], [ActionRequest("email_summary", "a@example.com", "Subj", "Body")]
    )
    dispatcher = OutboxDispatcher(store, Settings(smtp_host="mailpit", smtp_port=1025))
    assert await dispatcher.deliver_once() == 0  # first attempt fails, row stays pending
    assert (await store.outbox_for("s1"))[0].status == "pending"
    assert await dispatcher.deliver_once() == 1
    assert sent == [("a@example.com", "mailpit", 1025)]
    assert (await store.outbox_for("s1"))[0].status == "sent"
    assert await dispatcher.deliver_once() == 0  # never delivered twice


async def test_outbox_gives_up_after_five_attempts(
    store: SQLiteStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aiosmtplib

    async def always_fail(message: Any, **kwargs: Any) -> None:
        raise aiosmtplib.SMTPConnectError("down")

    monkeypatch.setattr(aiosmtplib, "send", always_fail)
    await store.commit("s1", 0, "{}", 1, [], [ActionRequest("email_summary", "a@example.com", "S", "B")])
    dispatcher = OutboxDispatcher(store, Settings(smtp_host="mailpit"))
    for _ in range(7):
        await dispatcher.deliver_once()
    row = (await store.outbox_for("s1"))[0]
    assert row.status == "failed" and row.attempts == 5


def test_json_logs_scrub_pii_and_carry_request_id() -> None:
    token = request_id_var.set("req-1")
    try:
        record = logging.makeLogRecord(
            {
                "msg": "caller margaret@email.com phone 650-521-2836",
                "levelname": "INFO",
                "name": "t",
                "path": "/v1",
            }
        )
        out = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert out["msg"] == "caller [email] phone [number]"
    assert out["request_id"] == "req-1" and out["path"] == "/v1"
    assert scrub("DOB 1985-03-15") == "DOB [number]"


def test_tracing_is_noop_without_endpoint() -> None:
    from claimbot.obs import tracing

    tracing.configure_tracing(None)
    assert tracing._configured is False
