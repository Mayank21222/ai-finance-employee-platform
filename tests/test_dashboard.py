"""Dashboard skeleton: sessions CRUD, runs shell, static assets."""

import os
import tempfile
from pathlib import Path

os.environ["DASH_DB"] = str(Path(tempfile.mkdtemp()) / "dash_test.db")

from fastapi.testclient import TestClient  # noqa: E402

from platform.dashboard import db  # noqa: E402
from platform.dashboard.app import app  # noqa: E402

db.init_db()  # TestClient without a lifespan context does not run startup hooks
client = TestClient(app)


def test_root_redirects_to_run_console():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/runs"


def test_run_console_renders_with_session_dropdown():
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert "Run console" in resp.text
    assert "acme" in resp.text  # seeded default session


def test_sessions_page_lists_seed_and_form():
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert "Sessions" in resp.text and "acme" in resp.text
    assert 'action="/sessions"' in resp.text


def test_session_create_edit_and_delete_cycle():
    resp = client.post("/sessions", data={
        "tenant": "globex", "currency": "EUR",
        "approval_threshold": "1200.5", "user_role": "viewer",
    }, follow_redirects=False)
    assert resp.status_code == 303
    listing = client.get("/sessions").text
    assert "globex" in listing and "1200.5" in listing

    # find the new session id from the delete form
    import re
    ids = [int(m) for m in re.findall(r'/sessions/(\d+)/delete', listing)]
    assert len(ids) == 2
    resp = client.post(f"/sessions/{ids[-1]}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert "globex" not in client.get("/sessions").text


def test_session_create_rejects_bad_threshold():
    resp = client.post("/sessions", data={
        "tenant": "x", "currency": "USD",
        "approval_threshold": "not-a-number", "user_role": "op",
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert "must be a number" in resp.text


def test_htmx_asset_served():
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert "htmx" in resp.text


# --- run console -----------------------------------------------------------

import json  # noqa: E402
import shutil  # noqa: E402
import time  # noqa: E402

from platform.dashboard import runner  # noqa: E402

MSG_FLOW = {
    "version": 1,
    "start_node_id": "m1",
    "session_context": {"tenant": "acme", "currency": "INR",
                        "approval_threshold": 50000.0,
                        "user_role": "finance_operator"},
    "nodes": [
        {"type": "message", "node_id": "m1", "template": "hello console",
         "next_node_ids": ["e"]},
        {"type": "end", "node_id": "e"},
    ],
}


def _wait_terminal(run_id: str, timeout: float = 15.0) -> runner.RunHandle:
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = runner.get(run_id)
        if h is not None and h.terminal:
            return h
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} did not finish in {timeout}s")


def test_full_run_lifecycle_through_console(tmp_path):
    flow_path = tmp_path / "msg_flow.json"
    flow_path.write_text(json.dumps(MSG_FLOW))
    os.environ["COMP_OPS_FLOW"] = str(flow_path)
    os.environ["COMP_OPS_SKIP_APP"] = "1"
    try:
        resp = client.post("/runs", data={
            "session_id": "1", "task": "say hello",
        }, follow_redirects=False)
        assert resp.status_code == 303
        run_id = resp.headers["location"].rsplit("/", 1)[-1]

        # detail page while running
        detail = client.get(f"/runs/{run_id}")
        assert detail.status_code == 200
        assert "say hello" in detail.text and "live trace" in detail.text

        handle = _wait_terminal(run_id)
        assert handle.status == "done"

        # report persisted and shown
        detail = client.get(f"/runs/{run_id}").text
        assert "final report" in detail

        # SSE replays the trace then closes with __done__
        sse = client.get(f"/runs/{run_id}/events")
        assert sse.status_code == 200
        assert sse.headers["content-type"].startswith("text/event-stream")
        assert "__done__" in sse.text and "run_started" in sse.text

        # history table links to the run
        listing = client.get("/runs").text
        assert run_id in listing
    finally:
        os.environ.pop("COMP_OPS_FLOW", None)
        os.environ.pop("COMP_OPS_SKIP_APP", None)


def test_start_run_rejected_while_another_is_active():
    fake = runner.RunHandle(run_id="fake-active", task="t", session_id=1)
    runner._RUNS["fake-active"] = fake
    try:
        resp = client.post("/runs", data={"session_id": "1", "task": "x"},
                           follow_redirects=False)
        assert resp.status_code == 409
        assert "already active" in resp.text

        # answering a run that is not waiting -> 400, queue untouched
        resp = client.post("/runs/fake-active/answer",
                           data={"answer": "y"}, follow_redirects=False)
        assert resp.status_code == 400
        assert fake.answers.empty()
    finally:
        runner._RUNS.pop("fake-active", None)


def test_unknown_run_detail_404():
    resp = client.get("/runs/nope-not-here")
    assert resp.status_code == 404


# --- agents view -----------------------------------------------------------


def test_agent_states_from_synthetic_trace():
    from ai_operator.graph import load_default_flow
    from ai_operator.tracing import RUNS_ROOT
    from platform.dashboard.app import _agent_states

    run_id = "test_synth_agents"
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        (run_dir / "trace.jsonl").write_text("\n".join(json.dumps(e) for e in [
            {"event": "node_entered", "node": "ap_agent",
             "node_type": "agent"},
            {"event": "decision", "thought": "looking up the invoice",
             "action_type": "tool_call"},
            {"event": "variable_written", "node": "ap_agent",
             "name": "ap_answer", "new": "42500 INR due 2026-07-30"},
        ]) + "\n")
        flow = load_default_flow()
        states = {s["node_id"]: s for s in _agent_states(run_id, flow, False)}
        ap = states["ap_agent"]
        assert ap["status"] == "complete"
        assert ap["answer"] == "ap_answer = 42500 INR due 2026-07-30"
        assert "looking up" in ap["reasoning"]
        assert "click" in ap["all_tools"] and len(ap["tools"]) == 10

        (run_dir / "trace.jsonl").write_text("")
        idle = {s["node_id"]: s
                for s in _agent_states(run_id, load_default_flow(), True)}
        assert idle["ap_agent"]["status"] == "idle"

        running_trace = "\n".join(json.dumps(e) for e in [
            {"event": "node_entered", "node": "ap_agent",
             "node_type": "agent"},
            {"event": "decision", "thought": "checking payables"},
        ]) + "\n"
        (run_dir / "trace.jsonl").write_text(running_trace)
        running = {s["node_id"]: s
                   for s in _agent_states(run_id, load_default_flow(), True)}
        assert running["ap_agent"]["status"] == "running"
        assert "checking payables" in running["ap_agent"]["reasoning"]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def test_tool_toggle_roundtrip_on_shipped_config():
    import json as _json

    from ai_operator.graph import DEFAULT_FLOW_PATH

    original = DEFAULT_FLOW_PATH.read_text()
    try:
        resp = client.post("/agents/ap_agent/tools/click")
        assert resp.status_code == 200
        assert "click (off)" in resp.text
        cfg = _json.loads(DEFAULT_FLOW_PATH.read_text())
        agent = next(n for n in cfg["nodes"] if n["node_id"] == "ap_agent")
        assert "click" not in agent["tools_enabled"]

        resp = client.post("/agents/ap_agent/tools/click")
        assert resp.status_code == 200
        cfg = _json.loads(DEFAULT_FLOW_PATH.read_text())
        agent = next(n for n in cfg["nodes"] if n["node_id"] == "ap_agent")
        assert "click" in agent["tools_enabled"]
    finally:
        if DEFAULT_FLOW_PATH.read_text() != original:
            DEFAULT_FLOW_PATH.write_text(original)


def test_tool_toggle_rejects_unknown_tool():
    resp = client.post("/agents/ap_agent/tools/not_a_tool")
    assert resp.status_code == 404


# --- flow editor / agents / messages / documents ---------------------------

from ai_operator.graph import DEFAULT_FLOW_PATH as SHIPPED_CONFIG  # noqa: E402


def _get_cfg():
    import json as _j
    return _j.loads(SHIPPED_CONFIG.read_text())


def test_all_nav_pages_render():
    for path, marker in [("/flow", "Flow editor"),
                         ("/agents", "Agents"),
                         ("/messages", "Message nodes"),
                         ("/documents", "Documents")]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert marker in resp.text, path
    flow = client.get("/flow").text
    assert "mermaid.min.js" in flow and "flowchart LR" in flow
    assert 'hx-post="/flow/validate"' in flow


def test_validate_endpoint_reports_valid_shipped_flow():
    resp = client.post("/flow/validate")
    assert resp.status_code == 200
    assert "Flow is valid" in resp.text


def test_flow_connection_add_and_remove_roundtrip():
    original = SHIPPED_CONFIG.read_text()
    try:
        resp = client.post("/flow/connections", data={
            "source": "ap_agent", "destination": "report"},
            follow_redirects=False)
        assert resp.status_code == 303
        cfg = _get_cfg()
        ap = next(n for n in cfg["nodes"] if n["node_id"] == "ap_agent")
        assert "report" in ap["next_node_ids"]

        resp = client.post("/flow/connections/remove", data={
            "source": "ap_agent", "destination": "report"},
            follow_redirects=False)
        assert resp.status_code == 303
        cfg = _get_cfg()
        ap = next(n for n in cfg["nodes"] if n["node_id"] == "ap_agent")
        assert "report" not in ap["next_node_ids"]
    finally:
        if SHIPPED_CONFIG.read_text() != original:
            SHIPPED_CONFIG.write_text(original)


def test_flow_connection_rejected_for_verify_node():
    original = SHIPPED_CONFIG.read_text()
    try:
        resp = client.post("/flow/connections", data={
            "source": "check_invoice", "destination": "report"},
            follow_redirects=False)
        assert resp.status_code == 400
        assert "agent and message" in resp.text
        assert SHIPPED_CONFIG.read_text() == original
    finally:
        if SHIPPED_CONFIG.read_text() != original:
            SHIPPED_CONFIG.write_text(original)


def test_agent_edit_and_duplicate_create():
    original = SHIPPED_CONFIG.read_text()
    try:
        resp = client.post("/agents/ap_agent", data={
            "system_prompt": "prompts/agent_ap.md",
            "instructions": "edited instructions",
            "save_as": "ap_answer", "fallback_next": "report"},
            follow_redirects=False)
        assert resp.status_code == 303
        ap = next(n for n in _get_cfg()["nodes"]
                  if n["node_id"] == "ap_agent")
        assert ap["instructions"] == "edited instructions"

        resp = client.post("/agents", data={
            "node_id": "ap_agent", "next_node_ids": "report",
            "fallback_next": ""}, follow_redirects=False)
        assert resp.status_code == 400
        assert "already exists" in resp.text
    finally:
        if SHIPPED_CONFIG.read_text() != original:
            SHIPPED_CONFIG.write_text(original)


def test_messages_page_empty_state_and_edit_404():
    resp = client.get("/messages")
    assert resp.status_code == 200
    assert "No message nodes" in resp.text
    resp = client.post("/messages/ghost", data={
        "template": "x", "next_node_id": "e", "fallback_next": ""},
        follow_redirects=False)
    assert resp.status_code == 404


def test_documents_upload_attach_cycle():
    import io

    original = SHIPPED_CONFIG.read_text()
    tenant_dir = SHIPPED_CONFIG.parents[1] / "company_data" / "acme"
    uploaded = tenant_dir / "zz_dash_test_note.txt"
    try:
        resp = client.post("/documents/upload", data={"tenant": "acme"}, files={
            "file": ("zz_dash_test_note.txt",
                     io.BytesIO(b"dash test content"), "text/plain")},
            follow_redirects=False)
        assert resp.status_code == 303
        assert uploaded.is_file()

        resp = client.post("/documents/attach", data={
            "tenant": "acme", "filename": "zz_dash_test_note.txt",
            "agents": ["ap_agent"]}, follow_redirects=False)
        assert resp.status_code == 303
        ap = next(n for n in _get_cfg()["nodes"]
                  if n["node_id"] == "ap_agent")
        assert "company_data/acme/zz_dash_test_note.txt" in ap["documents"]

        page = client.get("/documents?tenant=acme").text
        assert "zz_dash_test_note.txt" in page

        resp = client.post("/documents/attach", data={
            "tenant": "acme", "filename": "zz_dash_test_note.txt"},  # unchecked
            follow_redirects=False)
        assert resp.status_code == 303
        ap = next(n for n in _get_cfg()["nodes"]
                  if n["node_id"] == "ap_agent")
        assert "company_data/acme/zz_dash_test_note.txt" not in ap["documents"]
    finally:
        if SHIPPED_CONFIG.read_text() != original:
            SHIPPED_CONFIG.write_text(original)
        uploaded.unlink(missing_ok=True)


def test_upload_rejects_bad_extension():
    import io

    resp = client.post("/documents/upload", data={"tenant": "acme"}, files={
        "file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]


def test_mermaid_diagram_marks_edges():
    from platform.dashboard.html import mermaid_diagram

    out = mermaid_diagram(_get_cfg())
    assert "start([start]) --> ap_agent" in out
    assert "ap_agent -. fallback .-> report" in out
    assert "check_invoice -- match -->" in out
    assert "check_invoice -- mismatch -->" in out


def test_message_node_create_cycle():
    original = SHIPPED_CONFIG.read_text()
    try:
        resp = client.post("/messages", data={
            "node_id": "ask_amount", "template": "Need amount for {{vars.x}}",
            "next_node_id": "ask_amount", "fallback_next": "report"},
            follow_redirects=False)
        assert resp.status_code == 303
        node = next(n for n in _get_cfg()["nodes"]
                    if n["node_id"] == "ask_amount")
        assert node["type"] == "message"
        assert node["next_node_ids"] == ["ask_amount"]  # self-loop allowed

        page = client.get("/messages").text
        assert "ask_amount" in page and "Need amount for" in page

        # a template var with no prior save_as is rejected - the validator
        # catches it when the node becomes reachable from the start node
        resp = client.post("/messages", data={
            "node_id": "ask_due", "template": "{{vars.ghost}}",
            "next_node_id": "report", "fallback_next": ""},
            follow_redirects=False)
        assert resp.status_code == 303  # still unreachable -> accepted
        resp = client.post("/flow/connections", data={
            "source": "ap_agent", "destination": "ask_due"},
            follow_redirects=False)
        assert resp.status_code == 400
        assert "ghost" in resp.text
    finally:
        if SHIPPED_CONFIG.read_text() != original:
            SHIPPED_CONFIG.write_text(original)
