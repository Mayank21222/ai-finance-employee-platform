"""CLI runner: streams the trace, pauses for approvals, prints the final report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ai_operator.graph import build_graph
from ai_operator.llm import get_client
from ai_operator.session import SessionContext
from ai_operator.state import INITIAL_STATE_KEYS
from ai_operator.tracing import TraceLogger, unregister

DEFAULT_TASK = (
    "Process the latest invoice from Acme Corp: enter the amount and due date "
    "into the payables system and tell me when it's done."
)
REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_DB = REPO_ROOT / "checkpoints.db"


def _print_event(event: dict[str, Any]) -> None:
    kind = event.get("event", "?")
    if kind == "decision":
        print(f"  [step {event.get('step')}] decision: {event.get('action_type')} "
              f"{event.get('tool_name') or ''} {event.get('tool_args') or ''}")
    elif kind == "tool_result":
        status = "ok" if event.get("ok") else "FAIL"
        print(f"  [step {event.get('step')}] {event.get('tool')} -> {status} "
              f"({event.get('attempts')} attempt(s))")
    elif kind == "verification":
        print(f"  verification: {'MATCHED' if event.get('matched') else 'MISMATCH'}")
    elif kind == "finish":
        print(f"  finish: {event.get('status')}")
    else:
        print(f"  [{kind}]")


def _expected_build_hash() -> str:
    import hashlib

    return hashlib.sha256((REPO_ROOT / "mock_app" / "app.py").read_bytes()).hexdigest()[:16]


def _server_build_hash(url: str) -> str | None:
    """build_hash served at /health, or None if unreachable / pre-fix server."""
    try:
        resp = httpx.get(f"{url}/health", timeout=2.0)
        data = resp.json()
        return str(data["build_hash"]) if resp.status_code == 200 else None
    except Exception:  # noqa: BLE001 - connection errors, 404, non-JSON body
        return None


def _reachable(url: str) -> bool:
    try:
        httpx.get(url, timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


def _kill_listener(url: str) -> bool:
    """Terminate whatever process is listening on the app URL's port."""
    import signal

    port = url.rsplit(":", 1)[-1]
    try:
        out = subprocess.run(
            ["lsof", "-sTCP:LISTEN", "-ti", f"tcp:{port}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    pids = [int(p) for p in out.stdout.split() if p.isdigit()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
    return bool(pids)


def _ensure_app(url: str, start_app: bool) -> subprocess.Popen | None:
    expected = _expected_build_hash()
    current = _server_build_hash(url)
    if current == expected:
        return None  # serving the current source: safe to reuse

    if _reachable(url):
        if current is None:
            reason = "no /health endpoint (pre-fix server)"
        else:
            reason = f"stale build {current}, expected {expected}"
        if not start_app:
            raise SystemExit(
                f"mock app at {url} is stale ({reason}); kill it manually and "
                "restart, or drop --no-start-app"
            )
        print(f"mock app at {url} is stale ({reason}); killing it")
        if not _kill_listener(url):
            raise SystemExit(f"could not kill the stale mock app on {url}; "
                             "kill it manually and retry")
        for _ in range(20):
            if not _reachable(url):
                break
            time.sleep(0.25)
        else:
            raise SystemExit(f"stale mock app on {url} did not stop; "
                             "kill it manually and retry")
    elif not start_app:
        raise SystemExit(f"mock app is not reachable at {url}; start it first "
                         "or drop --no-start-app")

    port = url.rsplit(":", 1)[-1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_app.app:app", "--port", str(port),
         "--log-level", "warning"],
        cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if _server_build_hash(url) == expected:
            print(f"started mock app at {url} (pid {proc.pid}, build {expected})")
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise SystemExit(f"mock app failed to start at {url}")


def _set_failures(url: str, modes: list[str]) -> None:
    if modes:
        httpx.post(f"{url}/debug/failures", json={"modes": modes}, timeout=5.0)
        print(f"failure switches enabled: {modes}")
    else:
        httpx.post(f"{url}/debug/reset", timeout=5.0)


def run(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the AI operator on a business request.")
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK)
    parser.add_argument("--app-url", default=os.environ.get("APP_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--failures", default="",
                        help="comma-separated: popup,renamed_field,slow,validation")
    parser.add_argument("--fresh", action="store_true", help="clear payables records first")
    parser.add_argument("--no-start-app", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--answer", default=None,
                        help="non-interactive response to approval/clarification prompts "
                             "(e.g. y, n, or a sentence)")
    args = parser.parse_args(argv)

    run_id = args.run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    logger = TraceLogger(run_id, on_event=_print_event)
    print(f"run_id={run_id}  provider={os.environ.get('MODEL_PROVIDER', 'stub')}  "
          f"trace={logger.trace_path}")

    proc = _ensure_app(args.app_url, start_app=not args.no_start_app)
    _set_failures(args.app_url, [m.strip() for m in args.failures.split(",") if m.strip()])
    if args.fresh:
        from mock_app import db

        db.reset()
        print("payables records cleared")

    logger.event("run_started", task=args.task, app_url=args.app_url,
                 provider=os.environ.get("MODEL_PROVIDER", "stub"),
                 failures=args.failures)
    os.environ.setdefault("APP_URL", args.app_url)

    from ai_operator.tools import browser

    browser.set_evidence_dir(logger.evidence_dir)
    client = get_client()
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        graph = build_graph(client, checkpointer=saver)
        session = SessionContext()
        config = {
            "configurable": {"thread_id": run_id, "session_context": session},
            "recursion_limit": 400,
        }
        payload: Any = {"task": args.task, "run_id": run_id, **INITIAL_STATE_KEYS}
        try:
            while True:
                interrupt_payload = None
                for chunk in graph.stream(payload, config, stream_mode="updates"):
                    if "__interrupt__" in chunk:
                        interrupt_payload = chunk["__interrupt__"][0].value
                if interrupt_payload is None:
                    break
                kind = interrupt_payload.get("kind", "clarification")
                event = "approval_requested" if kind == "approval" else "human_input_requested"
                logger.event(event, **interrupt_payload)
                print(f"\n{interrupt_payload.get('question', interrupt_payload)}")
                if args.answer is not None:
                    answer = args.answer
                    print(f"> {answer} (from --answer)")
                else:
                    try:
                        answer = input("> ").strip()
                    except EOFError:
                        logger.event("human_input_eof", **interrupt_payload)
                        print(" (no stdin available; answering 'n')")
                        answer = "n"
                logger.event("human_input", kind=kind, answer=answer)
                payload = Command(resume=answer)
            final_state = graph.get_state(config).values
        except KeyboardInterrupt:
            logger.event("interrupted_by_user")
            print("\ninterrupted by user")
            browser.shutdown()
            if proc:
                proc.terminate()
            unregister(run_id)
            return 130
        report = final_state.get("report")

    if report is None:
        report = {"status": "failed", "summary": "graph produced no report",
                  "run_id": run_id, "task": args.task, "actions": [], "approvals": [],
                  "evidence": [], "errors": ["no report produced"]}
        logger.write_report(report)
    browser.shutdown()
    if proc:
        proc.terminate()
    unregister(run_id)
    print("\n=== FINAL REPORT ===")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("status") == "verified_complete" else 1


if __name__ == "__main__":
    raise SystemExit(run())
