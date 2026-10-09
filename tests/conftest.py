"""Shared fixtures: run the sandbox app on a random port with a temp DB."""

import socket
import threading
import time

import httpx
import pytest
import uvicorn

import mock_app
from ai_operator.runtime import Operator


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """Keep every test's trace log inside tmp so real .data/logs stays clean."""
    import ai_operator.observability as obs

    monkeypatch.setattr(obs, "_configured", True)
    monkeypatch.setattr(obs, "_log_dir", str(tmp_path))
    monkeypatch.setattr(obs, "_event_path", str(tmp_path / "traces.jsonl"))
    yield


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    mock_app.DB_PATH = str(tmp_path_factory.mktemp("payables") / "payables.db")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(mock_app.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.05)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def reset_db(app_server):
    httpx.post(app_server + "/api/reset", timeout=5)
    yield


def drive(op: Operator, task: str, answers: list | None = None) -> dict:
    """Run to completion, auto-answering interrupts."""
    answers = answers or []
    result = op.start(task)
    run_id = result["run_id"]
    i = 0
    while True:
        pending = Operator.suspended(result)
        if not pending:
            break
        answer = answers[i] if i < len(answers) else "yes"
        result = op.resume(run_id, answer)
        i += 1
    return result
