"""Phase 6 section 2: versioned HTTP API, API keys, and the MCP server.

Mandated coverage (Improvements.md Section 2):
  * calling the API without a key returns 401;
  * a key with the `auditor` role cannot start a run;
  * a valid key starts a stub run and GET /api/v1/runs/{id} shows the same
    report as the console (report.json on disk);
  * approvals list + answer endpoint enforce the key's role (403 denial,
    traced as approval_denied_role);
  * the MCP server is exercised with a scripted JSON-RPC client (NOT a real
    third-party MCP client - reported as such).
"""

import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ["DASH_DB"] = str(Path(tempfile.mkdtemp()) / "phase6_api.db")
os.environ["STUB_MODEL"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from ai_operator.tracing import RUNS_ROOT  # noqa: E402
from platform.dashboard import db, runner  # noqa: E402
from platform.dashboard.app import app  # noqa: E402

db.init_db()
client = TestClient(app)

API_PORT = 8014
API_URL = f"http://127.0.0.1:{API_PORT}"

MIN_AGENT_FLOW = {
    "start_node_id": "cls",
    "session_context": {"tenant": "acme", "currency": "INR",
                        "approval_threshold": 50000.0,
                        "user_role": "finance_operator"},
    "nodes": [
        {"type": "agent", "node_id": "cls",
         "instructions": "Available specialists: end. Route unknown to end.",
         "next_node_ids": ["finish_line"]},
        {"type": "end", "node_id": "finish_line"},
    ],
}


def _key(label: str, role_name: str) -> str:
    role = db.get_role(role_name)
    assert role is not None, role_name
    return db.create_api_key(label, role["id"])[1]


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


# --- auth: no key, bad key ---------------------------------------------------

def test_api_requires_a_key():
    resp = client.post("/api/v1/runs", json={"session_id": 1, "task": "x"})
    assert resp.status_code == 401
    assert "API key" in resp.json()["detail"]
    resp = client.get("/api/v1/runs/anything")
    assert resp.status_code == 401
    resp = client.post("/api/v1/runs",
                       json={"session_id": 1, "task": "x"},
                       headers=_auth("fin_bogus_key"))
    assert resp.status_code == 401


# --- API keys page: create (shown once) / revoke -----------------------------

def test_api_keys_page_create_and_revoke():
    body = client.get("/apikeys").text
    assert "API keys" in body and "/apikeys" in body
    resp = client.post("/apikeys", data={"label": "probe key",
                                         "role_id": str(db.get_role("auditor")["id"])})
    assert resp.status_code == 200
    match = re.search(r"fin_[A-Za-z0-9_\-]+", resp.text)
    assert match, "the plain key must be shown once at creation"
    key = match.group(0)
    # active key authenticates (404 for a missing run, not 401)
    assert client.get("/api/v1/runs/nope", headers=_auth(key)).status_code == 404
    # second render does not leak the key again
    assert key not in client.get("/apikeys").text
    row = next(k for k in db.list_api_keys() if k["label"] == "probe key")
    resp = client.post(f"/apikeys/{row['id']}/revoke", follow_redirects=False)
    assert resp.status_code == 303
    assert client.get("/api/v1/runs/nope", headers=_auth(key)).status_code == 401


# --- role gate: auditor cannot start runs ------------------------------------

def test_api_auditor_key_cannot_start_run():
    key = _key("auditor api key", "auditor")
    before = len(db.list_runs(500))
    resp = client.post("/api/v1/runs",
                       json={"session_id": 1, "task": "start something"},
                       headers=_auth(key))
    assert resp.status_code == 403, resp.text
    assert "cannot start runs" in resp.json()["detail"]
    assert len(db.list_runs(500)) == before  # nothing started


# --- a valid key starts a stub run; API report == console report -------------

def test_api_valid_key_starts_stub_run_and_matches_console_report():
    flow_path = Path(tempfile.mkdtemp()) / "api_flow.json"
    flow_path.write_text(json.dumps(MIN_AGENT_FLOW), encoding="utf-8")
    os.environ["COMP_OPS_FLOW"] = str(flow_path)
    os.environ["COMP_OPS_SKIP_APP"] = "1"
    key = _key("operator api key", "finance_operator")
    try:
        resp = client.post("/api/v1/runs",
                           json={"session_id": 1, "task": "say hi"},
                           headers=_auth(key))
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["run_id"]
        assert run_id in client.get("/runs").text  # console lists it too

        deadline = time.time() + 30
        while time.time() < deadline:
            handle = runner.get(run_id)
            if handle is not None and handle.terminal:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("run did not finish")

        api = client.get(f"/api/v1/runs/{run_id}", headers=_auth(key))
        assert api.status_code == 200
        payload = api.json()
        report = json.loads((RUNS_ROOT / run_id / "report.json").read_text())
        # same report the console renders (the console reads report.json)
        assert payload["report"] == report
        assert payload["status"] == report["status"]
        assert payload["tokens"]["input"] == report["tokens"]["input"]
        assert payload["tokens"]["output"] == report["tokens"]["output"]
        assert payload["state"] == db.get_run(run_id)["state"]
        # variables visible to external agents
        assert isinstance(payload["variables"], dict)
        # the SSE endpoint streams under a key too
        sse = client.get(f"/api/v1/runs/{run_id}/events", headers=_auth(key))
        assert sse.status_code == 200
        assert sse.headers["content-type"].startswith("text/event-stream")
        assert "__done__" in sse.text
    finally:
        os.environ.pop("COMP_OPS_FLOW", None)
        os.environ.pop("COMP_OPS_SKIP_APP", None)


# --- approvals: pending list, key-role answer gate ---------------------------

def _insert_waiting_approval() -> str:
    run_id = "p6api_wait_" + uuid.uuid4().hex[:8]
    db.record_run(run_id, 1, "pay 75000")
    db.set_state(run_id, "waiting_approval")
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"time": "T", "event": "run_started", "task": "pay 75000"},
        {"time": "T", "event": "approval_requested", "kind": "approval",
         "tool_name": "create_invoice", "tool_args": {"amount": "75000"},
         "reason": "amount 75000 exceeds approval threshold 50000",
         "question": "Create invoice for 75000?"},
    ]) + "\n")
    handle = runner.RunHandle(run_id=run_id, task="pay 75000", session_id=1)
    handle.status = "waiting_input"
    handle.interrupt = {"kind": "approval", "tool_name": "create_invoice",
                        "tool_args": {"amount": "75000"},
                        "reason": "amount 75000 exceeds approval threshold",
                        "question": "Create invoice for 75000?"}
    runner._RUNS[run_id] = handle
    return run_id


def test_api_approvals_list_and_role_gated_answer():
    run_id = _insert_waiting_approval()
    op = _key("approvals operator", "finance_operator")
    auditor = _key("approvals auditor", "auditor")
    try:
        # pending list (both the versioned path and the spec-literal alias)
        for path in (f"/api/v1/approvals?state=pending",
                     f"/approvals?state=pending"):
            resp = client.get(path, headers=_auth(op))
            assert resp.status_code == 200, path
            pending = resp.json()["approvals"]
            hit = next(p for p in pending if p["run_id"] == run_id)
            assert hit["tool_name"] == "create_invoice"
            assert "threshold" in (hit.get("reason") or "")
        # no key -> 401
        assert client.get("/api/v1/approvals").status_code == 401

        # auditor cannot approve: 403, run still waiting, refusal traced
        resp = client.post(f"/api/v1/runs/{run_id}/answer",
                           json={"answer": "approve"}, headers=_auth(auditor))
        assert resp.status_code == 403, resp.text
        assert "requires a higher role" in resp.json()["detail"]
        assert db.get_run(run_id)["state"] == "waiting_approval"
        events = [json.loads(l) for l in
                  (RUNS_ROOT / run_id / "trace.jsonl").read_text().splitlines()]
        denials = [e for e in events if e["event"] == "approval_denied_role"]
        assert denials and denials[0]["role"] == "auditor"
        assert denials[0]["amount"] == 75000

        # finance_operator is within the limit: the answer reaches the run
        resp = client.post(f"/api/v1/runs/{run_id}/answer",
                           json={"answer": "approve"}, headers=_auth(op))
        assert resp.status_code == 200, resp.text
        assert runner._RUNS[run_id].answers.get_nowait() == "approve"
    finally:
        shutil.rmtree(RUNS_ROOT / run_id, ignore_errors=True)
        runner._RUNS.pop(run_id, None)
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


# --- MCP server with a scripted client ---------------------------------------

def _read_json_line(proc: subprocess.Popen, timeout: float = 15.0) -> dict:
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    assert ready, "timed out waiting for an MCP reply"
    line = proc.stdout.readline()
    assert line, "MCP server closed stdout"
    return json.loads(line)


def _rpc(proc: subprocess.Popen, msg: dict, timeout: float = 15.0) -> dict:
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return _read_json_line(proc, timeout)


def test_mcp_server_with_scripted_client():
    """The MCP server is tested with a scripted JSON-RPC client, not a
    third-party MCP host (reported honestly in the phase report)."""
    flow_path = Path(tempfile.mkdtemp()) / "mcp_flow.json"
    flow_path.write_text(json.dumps(MIN_AGENT_FLOW), encoding="utf-8")
    env = {**os.environ,
           "DASH_DB": os.environ["DASH_DB"],
           "STUB_MODEL": "1",
           "COMP_OPS_FLOW": str(flow_path),
           "COMP_OPS_SKIP_APP": "1"}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "platform.dashboard.app:app",
         "--port", str(API_PORT), "--log-level", "warning"],
        cwd=_REPO_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    mcp = None
    try:
        for _ in range(80):
            try:
                httpx.get(f"{API_URL}/runs", timeout=1.0)
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            pytest.fail("dashboard did not start for the MCP test")

        key = _key("mcp bridge", "finance_operator")
        mcp = subprocess.Popen(
            [sys.executable, "-m", "platform.mcp_server"],
            cwd=_REPO_ROOT,
            env={**env, "FIN_API_KEY": key, "FIN_DASHBOARD_URL": API_URL},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

        init = _rpc(mcp, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05",
                                     "capabilities": {},
                                     "clientInfo": {"name": "scripted",
                                                    "version": "0"}}})
        assert init["result"]["serverInfo"]["name"] == "fin-mcp"
        mcp.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        mcp.stdin.flush()

        listed = _rpc(mcp, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in listed["result"]["tools"]}
        assert names == {"run_task", "get_run_status",
                         "list_pending_approvals", "answer_approval"}

        started = _rpc(mcp, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "run_task",
                                        "arguments": {"session_id": 1,
                                                      "task": "mcp hello"}}})
        assert not started["result"]["isError"], started
        run_id = re.search(r'"run_id":\s*"([^"]+)"',
                           started["result"]["content"][0]["text"]).group(1)

        deadline = time.time() + 30
        while time.time() < deadline:
            row = db.get_run(run_id)
            if row and row.get("finished_at"):
                break
            time.sleep(0.1)
        status = _rpc(mcp, {"jsonrpc": "2.0", "id": 4,
                            "method": "tools/call",
                            "params": {"name": "get_run_status",
                                       "arguments": {"run_id": run_id}}})
        assert not status["result"]["isError"], status
        text = status["result"]["content"][0]["text"]
        assert run_id in text and '"status"' in text

        pending = _rpc(mcp, {"jsonrpc": "2.0", "id": 5,
                             "method": "tools/call",
                             "params": {"name": "list_pending_approvals",
                                        "arguments": {}}})
        assert not pending["result"]["isError"], pending
        assert '"approvals"' in pending["result"]["content"][0]["text"]
    finally:
        if mcp is not None:
            mcp.terminate()
            mcp.wait(timeout=10)
        server.terminate()
        server.wait(timeout=10)
