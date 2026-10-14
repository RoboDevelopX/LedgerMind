"""Structured logging: one JSON object per line, with event fields passed as keyword arguments."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, UTC
from typing import Any

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formats a record as JSON, including any `extra=` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_")})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the `ledgermind` logger tree (idempotent)."""
    root = logging.getLogger("ledgermind")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Logger under the `ledgermind` namespace."""
    return logging.getLogger(name if name.startswith("ledgermind") else f"ledgermind.{name}")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Log `event` with structured fields. Names that clash with LogRecord attributes (args, name...) get a `_`
    suffix."""
    logger.log(level, event, extra={f"{k}_" if k in _RESERVED else k: v for k, v in fields.items()})
