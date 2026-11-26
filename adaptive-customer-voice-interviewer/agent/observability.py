"""Structured logging and request-correlation helpers for CVI.

The module has no provider dependencies and writes only to stdout/stderr so
Docker (or a VM log driver) can own retention and shipping.
"""

import json
import logging
import os
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


_request_id: ContextVar[str | None] = ContextVar(
    "cvi_request_id",
    default=None,
)

_RESERVED_RECORD_FIELDS = {
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
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(value: str):
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record, including the active request ID."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                timezone.utc,
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_FIELDS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging() -> logging.Logger:
    """Configure root logging from CVI_LOG_FORMAT and CVI_LOG_LEVEL."""

    level = os.getenv("CVI_LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("CVI_LOG_FORMAT", "json").strip().lower()

    handler = logging.StreamHandler()

    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("httpx").setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
