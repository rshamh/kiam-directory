"""Structured logging.

One JSON object per line, so a log shipper indexes fields instead of regexing a
format string. `extra=` keys are merged in at the top level, which is what makes
an auth or evidence-access event queryable:

    logger.info("magic_link.issued", extra={"user_id": user.pk})

Never put a raw magic-link token, an evidence URL or a full email address in a
message or an extra — see accounts/services/magic_link.py.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

#: LogRecord attributes that are the record's own plumbing rather than payload.
#: Anything not in here arrived via `extra=` and belongs in the output.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JSONFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # `default=str` so a datetime, UUID or Decimal in an `extra=` cannot take
        # the process down at log time — a logging call must never raise.
        return json.dumps(payload, default=str)
