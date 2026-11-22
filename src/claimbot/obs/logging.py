"""Structured JSON logging with a request id and a last-line PII scrub."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DIGITS = re.compile(r"\d[\d ()./-]{5,}\d")
_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def scrub(value: str) -> str:
    return _DIGITS.sub("[number]", _EMAIL.sub("[email]", value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
        }
        if rid := request_id_var.get():
            out["request_id"] = rid
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                out[key] = scrub(value) if isinstance(value, str) else value
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
