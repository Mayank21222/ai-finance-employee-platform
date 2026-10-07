"""Run tracing: trace.jsonl events, evidence screenshots, final report.json.

Nodes call trace_event(); when no logger is registered (unit tests) it no-ops.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = _REPO_ROOT / "runs"

_loggers: dict[str, "TraceLogger"] = {}


class TraceLogger:
    def __init__(self, run_id: str, on_event: Callable[[dict[str, Any]], None] | None = None,
                 root: Path = RUNS_ROOT) -> None:
        self.run_id = run_id
        self.run_dir = root / run_id
        self.evidence_dir = self.run_dir / "evidence"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.run_dir / "trace.jsonl"
        self.report_path = self.run_dir / "report.json"
        self.on_event = on_event
        _loggers[run_id] = self

    def event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": time.time(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": self.run_id,
            "event": event_type,
            **fields,
        }
        with self.trace_path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        if self.on_event:
            self.on_event(record)
        return record

    def write_report(self, report: dict[str, Any]) -> Path:
        self.report_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
        self.event("report_written", path=str(self.report_path), status=report.get("status"))
        return self.report_path

    def events(self) -> list[dict[str, Any]]:
        if not self.trace_path.exists():
            return []
        return [json.loads(line) for line in self.trace_path.read_text().splitlines() if line]


def trace_event(run_id: str, event_type: str, **fields: Any) -> None:
    logger = _loggers.get(run_id)
    if logger is not None:
        logger.event(event_type, **fields)


def get_logger(run_id: str) -> TraceLogger | None:
    return _loggers.get(run_id)


def unregister(run_id: str) -> None:
    _loggers.pop(run_id, None)
