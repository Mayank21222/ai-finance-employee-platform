"""Observability: one place that records every trace.

Two sinks under STORAGE_DIR/logs:
  - app.log       human-readable rotating log (console + file)
  - traces.jsonl  machine-readable, one JSON event per line, run-correlated

The runtime, model client and dashboard emit events through `log_event`, so a
single run can be replayed end-to-end from the log alone.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler

_configured = False
_log_dir: str | None = None
_event_path: str | None = None

_current_run: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)


def set_run(run_id: str | None):
    """Bind the current thread/context to a run id for correlated logging."""
    _current_run.set(run_id)


def current_run() -> str | None:
    return _current_run.get()


def configure_logging(storage_dir: str | None = None, level: str | None = None, force: bool = False) -> str:
    """Idempotently configure the `ai_operator` logger. Returns the event path."""
    global _configured, _log_dir, _event_path
    if _configured and not force:
        return _event_path or ""
    storage_dir = storage_dir or os.getenv("STORAGE_DIR", ".data")
    _log_dir = os.path.join(storage_dir, "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _event_path = os.path.join(_log_dir, "traces.jsonl")

    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logger = logging.getLogger("ai_operator")
    logger.setLevel(getattr(logging, lvl, logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(os.path.join(_log_dir, "app.log"),
                                       maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    _configured = True
    return _event_path


def get_logger(name: str = "ai_operator") -> logging.Logger:
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def log_event(event: str, run_id: str | None = None, level: str = "info", **fields) -> None:
    """Append one structured event to traces.jsonl and a compact line to app.log."""
    if not _configured:
        configure_logging()
    payload = {"ts": time.time(), "event": event}
    rid = run_id or current_run()
    if rid:
        payload["run_id"] = rid
    for key, value in fields.items():
        if isinstance(value, str) and len(value) > 2000:
            value = value[:2000] + f"…(+{len(value) - 2000} chars)"
        payload[key] = value
    line = json.dumps(payload, default=str)
    log = logging.getLogger("ai_operator.trace")
    getattr(log, level, log.info)("%s", line)
    if _event_path:
        try:
            with open(_event_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def read_events(limit: int = 300, run_id: str | None = None) -> list[dict]:
    if not _configured:
        configure_logging()
    if not _event_path or not os.path.isfile(_event_path):
        return []
    events: list[dict] = []
    with open(_event_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id and rec.get("run_id") != run_id:
                continue
            events.append(rec)
    return events[-limit:]
