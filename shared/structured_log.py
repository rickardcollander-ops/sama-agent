"""
Structured (JSON) logger formatter.

Use ``configure_structured_logging()`` from ``main.py`` after ``basicConfig``
to switch the default formatter to JSON. Everything subsequent — uvicorn,
agent loggers, our own — emits one JSON line per record, which is what
Datadog and Sentry happily ingest.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    """One JSON object per log line. Includes ``ts``, ``level``, ``msg``,
    ``logger``, ``thread``, plus any ``extra`` fields the caller passed."""

    _RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except TypeError:
                payload[key] = repr(value)
        return json.dumps(payload, default=str)


def configure_structured_logging(level: str | int | None = None) -> None:
    """Replace the default handler's formatter with ``JsonFormatter`` if
    ``LOG_FORMAT=json`` (default in production) — otherwise leave the
    human-readable formatter in place for local development."""
    if os.getenv("LOG_FORMAT", "").lower() != "json":
        return
    root = logging.getLogger()
    if level is not None:
        root.setLevel(level)
    formatter = JsonFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
