"""Background run execution for the dashboard.

Same loop as ai_operator.run, but interrupt answers arrive over a queue
(the browser) instead of stdin, and the trace is consumed by SSE tailing.
One run is active at a time: concurrent runs would fight over the mock
app's payables data and the browser pool.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ai_operator.graph import load_default_flow
from ai_operator.llm import get_client
from ai_operator.run import CHECKPOINT_DB, _ensure_app, _set_failures
from ai_operator.state import INITIAL_STATE_KEYS
from ai_operator.tracing import TraceLogger, unregister
from platform.dashboard import db
from platform.flow.compiler import compile_flow
from platform.flow.models import Flow, load_flow

_ACTIVE_LOCK = threading.Lock()
_RUNS: dict[str, "RunHandle"] = {}
_SPAWNED_PROCS: list[subprocess.Popen] = []


class RunCancelled(Exception):
    pass


@dataclass
class RunHandle:
    run_id: str
    task: str
    session_id: int
    status: str = "running"  # running | waiting_input | done | failed | cancelled
    error: str | None = None
    interrupt: dict[str, Any] | None = None
    answers: queue.Queue = field(default_factory=queue.Queue)
    cancel: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    thread: threading.Thread | None = None

    @property
    def terminal(self) -> bool:
        return self.status in ("done", "failed", "cancelled")


def get(run_id: str) -> RunHandle | None:
    return _RUNS.get(run_id)


def active() -> RunHandle | None:
    with _ACTIVE_LOCK:
        for h in _RUNS.values():
            if not h.terminal:
                return h
    return None


def start_run(task: str, session_id: int, failures: str = "",
              fresh: bool = False, app_url: str | None = None) -> RunHandle:
    if active() is not None:
        raise RuntimeError("Another run is already active; wait for it to finish.")
    session = db.get_session(session_id)
    if session is None:
        raise RuntimeError(f"No session with id {session_id}.")
    run_id = (datetime.now().strftime("run_%Y%m%d_%H%M%S-")
              + uuid4().hex[:6])
    handle = RunHandle(run_id=run_id, task=task, session_id=session_id)
    with _ACTIVE_LOCK:
        _RUNS[run_id] = handle
    db.record_run(run_id, session_id, task)
    handle.thread = threading.Thread(
        target=_worker, args=(handle, session, failures, fresh, app_url),
        name=f"run-{run_id}", daemon=True,
    )
    handle.thread.start()
    return handle


def answer_run(run_id: str, answer: str) -> None:
    handle = _RUNS.get(run_id)
    if handle is None:
        raise RuntimeError("Unknown run.")
    if handle.status != "waiting_input":
        raise RuntimeError("This run is not waiting for input.")
    handle.answers.put(answer)


def cancel_run(run_id: str) -> None:
    handle = _RUNS.get(run_id)
    if handle is None or handle.terminal:
        return
    handle.cancel.set()
    handle.answers.put(None)  # unblock a waiting interrupt


def shutdown() -> None:
    """Terminate mock-app servers this dashboard spawned; free browsers."""
    for h in list(_RUNS.values()):
        cancel_run(h.run_id)
    for h in list(_RUNS.values()):
        if h.thread is not None:
            h.thread.join(timeout=15)
    for proc in _SPAWNED_PROCS:
        try:
            proc.terminate()
        except Exception:
            pass
    _SPAWNED_PROCS.clear()
    try:
        from ai_operator.tools import browser

        browser.shutdown()
    except Exception:
        pass


def _load_flow() -> Flow:
    """The shipped flow, or the COMP_OPS_FLOW override (tests / experiments)."""
    override = os.environ.get("COMP_OPS_FLOW")
    return load_flow(Path(override)) if override else load_default_flow()


def _session_context(session: dict[str, Any], flow: Flow):
    return flow.session_context.to_session_context().model_copy(update={
        "tenant": session["tenant"],
        "currency": session["currency"],
        "approval_threshold": float(session["approval_threshold"]),
        "user_role": session["user_role"],
    })


def _worker(handle: RunHandle, session: dict[str, Any], failures: str,
            fresh: bool, app_url: str | None) -> None:
    load_dotenv()
    app_url = app_url or os.environ.get("APP_URL", "http://127.0.0.1:8000")
    logger = TraceLogger(handle.run_id)
    try:
        failure_list = [m.strip() for m in failures.split(",") if m.strip()]
        if os.environ.get("COMP_OPS_SKIP_APP") != "1":
            proc = _ensure_app(app_url, start_app=True)
            if proc is not None:
                _SPAWNED_PROCS.append(proc)
            _set_failures(app_url, failure_list)
        if fresh:
            from mock_app import db as mock_db

            mock_db.reset()
        provider = os.environ.get("MODEL_PROVIDER", "stub")
        logger.event("run_started", task=handle.task, app_url=app_url,
                     provider=provider, failures=failures,
                     session_id=handle.session_id, tenant=session["tenant"])
        os.environ.setdefault("APP_URL", app_url)

        from ai_operator.tools import browser

        browser.set_evidence_dir(logger.evidence_dir)
        client = get_client()
        flow = _load_flow()
        context = _session_context(session, flow)
        with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
            graph = compile_flow(flow, client).compile(checkpointer=saver)
            config = {
                "configurable": {
                    "thread_id": handle.run_id,
                    "session_context": context,
                },
                "recursion_limit": 400,
            }
            payload: Any = {"task": handle.task, "run_id": handle.run_id,
                            **INITIAL_STATE_KEYS}
            while True:
                interrupt_payload = None
                for chunk in graph.stream(payload, config, stream_mode="updates"):
                    if handle.cancel.is_set():
                        raise RunCancelled
                    if "__interrupt__" in chunk:
                        interrupt_payload = chunk["__interrupt__"][0].value
                if interrupt_payload is None:
                    break
                kind = interrupt_payload.get("kind", "clarification")
                event = ("approval_requested" if kind == "approval"
                         else "human_input_requested")
                logger.event(event, **interrupt_payload)
                handle.interrupt = interrupt_payload
                handle.status = "waiting_input"
                answer = handle.answers.get()
                if answer is None:
                    raise RunCancelled
                logger.event("human_input", kind=kind, answer=answer)
                handle.interrupt = None
                handle.status = "running"
                payload = Command(resume=answer)
            final_state = graph.get_state(config).values

        report = final_state.get("report")
        if report is None:
            report = {
                "status": "failed", "summary": "graph produced no report",
                "run_id": handle.run_id, "task": handle.task, "actions": [],
                "approvals": [], "evidence": [],
                "errors": ["no report produced"],
            }
        logger.write_report(report)
        handle.status = "done"
        db.finish_run(handle.run_id, str(report.get("status", "done")))
    except RunCancelled:
        logger.event("cancelled_by_user")
        handle.status = "cancelled"
        db.finish_run(handle.run_id, "cancelled")
    except Exception as exc:
        handle.error = f"{type(exc).__name__}: {exc}"
        handle.status = "failed"
        logger.event("run_error", error=handle.error,
                     trace=traceback.format_exc()[-4000:])
        db.finish_run(handle.run_id, "failed", handle.error)
    finally:
        handle.finished_at = time.time()
        unregister(handle.run_id)
