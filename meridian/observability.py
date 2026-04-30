"""Trace correlation and logging setup for Meridian (Streamlit + MCP)."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "meridian_trace_id", default=None
)


def get_trace_id() -> str | None:
    return _trace_id.get()


def new_trace_id() -> str:
    """32-char hex id for one user turn or tool invocation chain."""
    return uuid.uuid4().hex


@contextmanager
def trace_scope(trace_id: str | None) -> Iterator[None]:
    """Bind ``trace_id`` for the current thread/async context."""
    if trace_id is None:
        yield
        return
    token = _trace_id.set(trace_id)
    try:
        yield
    finally:
        _trace_id.reset(token)


class _TraceIdFilter(logging.Filter):
    """Attach ``trace_id`` to every log record for formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        tid = get_trace_id()
        record.trace_id = tid if tid else "-"  # type: ignore[attr-defined]
        return True


class _JsonFormatter(logging.Formatter):
    """One JSON object per line (for log drains / Loki / Cloud Logging)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
        }
        for key in ("meridian_tool", "meridian_customer_suffix", "meridian_duration_ms"):
            if hasattr(record, key):
                val = getattr(record, key)
                payload[key.replace("meridian_", "")] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Idempotent: configure root handler once (level from ``MERIDIAN_LOG_LEVEL``)."""
    root = logging.getLogger()
    if getattr(root, "_meridian_configured", False):
        return
    level_name = (os.environ.get("MERIDIAN_LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_TraceIdFilter())
    fmt = (os.environ.get("MERIDIAN_LOG_FORMAT") or "text").lower().strip()
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] trace=%(trace_id)s %(message)s"
            )
        )
    root.addHandler(handler)
    setattr(root, "_meridian_configured", True)


def log_tool_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    tool: str,
    duration_ms: float | None = None,
    customer_id_suffix: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    """Emit one log line with standard tool fields on the record."""
    extra: dict[str, Any] = {
        "meridian_tool": tool,
    }
    if duration_ms is not None:
        extra["meridian_duration_ms"] = round(duration_ms, 3)
    if customer_id_suffix is not None:
        extra["meridian_customer_suffix"] = customer_id_suffix
    parts = [f"event={event!r}"]
    if extra_fields:
        for k, v in extra_fields.items():
            parts.append(f"{k}={v!r}")
    logger.log(level, " ".join(parts), extra=extra)
