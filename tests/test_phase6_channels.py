"""Phase 6 section 6: approval and alert channels.

Covers the two mandated tests (entering waiting_approval queues exactly one
notification; expired and reused one-time links are rejected) plus the alert
on bad endings, the role check the link cannot bypass, the channels page and
its test button, and a webhook delivered to a local capture server.

Delivery in these tests is a local capture / monkeypatched endpoint - this is
stub-level verification, never a real Slack webhook or SMTP server.
"""

import http.server
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_channels.db"))

import pytest  # noqa: E402

from ai_operator.tracing import RUNS_ROOT  # noqa: E402
from platform.dashboard import channels, db, runner  # noqa: E402
from platform.dashboard.app import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

db.init_db()
client = TestClient(app)


# --- helpers ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_channels():
    """Never leak channels/links into other modules' shared test DB."""
    yield
    with db.connect() as con:
        con.execute("DELETE FROM channels")
        con.execute("DELETE FROM notifications")
        con.execute("DELETE FROM approval_links")


def _session(role: str = "finance_operator") -> int:
    for s in db.list_sessions():
        if s["tenant"] == "acme" and s["user_role"] == role:
            return s["id"]
    return db.add_session("acme", "INR", 50000.0, role)


def _insert(run_id: str, sid: int, task: str) -> None:
    db.record_run(run_id, sid, task)
    db.set_state(run_id, "waiting_approval")


def _cleanup(run_id: str) -> None:
    shutil.rmtree(RUNS_ROOT / run_id, ignore_errors=True)
    runner._RUNS.pop(run_id, None)
    with db.connect() as con:
        con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM notifications WHERE run_id = ?", (run_id,))
        con.execute("DELETE FROM approval_links WHERE run_id = ?", (run_id,))


def _webhook_channel(url: str) -> int:
    return db.add_channel("test-hook", "webhook", {"url": url})


def _interrupt() -> dict:
    return {
        "kind": "approval", "tool_name": "create_invoice",
        "tool_args": {"amount": "75000", "vendor": "acme"},
        "reason": "amount 75000 exceeds approval threshold 50000",
        "question": "Create invoice for 75000?",
    }


class _FakeGraph:
    """Yields one approval interrupt, then runs to completion after resume."""

    def __init__(self, interrupt: dict):
        self._interrupt = interrupt
        self._first = True

    def stream(self, payload, config, stream_mode=None):
        if self._first:
            self._first = False
            yield {"__interrupt__": [SimpleNamespace(value=self._interrupt)]}
        else:
            yield {"ap_agent": {}}

    def get_state(self, config):
        return SimpleNamespace(values={"report": {"status": "failed"}})


# --- mandated test 1: exactly one notification per waiting_approval --------


def test_waiting_approval_queues_exactly_one_notification(monkeypatch):
    delivered: list[dict] = []

    def fake_webhook(config, text, payload):
        delivered.append({"config": config, "text": text, "payload": payload})

    monkeypatch.setattr(channels, "deliver_webhook", fake_webhook)
    run_id = "t_ch_one_notif"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    _webhook_channel("https://hooks.example.invalid/x")
    handle = runner.RunHandle(run_id=run_id, task="pay 75000 to Acme",
                              session_id=sid)
    handle.answers.put("approve")  # the interrupt is answered immediately
    runner._RUNS[run_id] = handle
    from ai_operator.tracing import TraceLogger
    logger = TraceLogger(run_id)
    try:
        final = runner._stream_loop(handle, _FakeGraph(_interrupt()), {},
                                    {}, logger)
        rows = db.list_notifications(run_id=run_id)
        # exactly ONE notification for this transition, already delivered
        assert len(rows) == 1
        assert rows[0]["kind"] == "waiting_approval"
        assert rows[0]["status"] == "sent"
        assert rows[0]["link"].startswith(
            f"{channels.base_url()}/approve/")
        # the message carries action + policy rule + link
        assert len(delivered) == 1
        text = delivered[0]["text"]
        assert "create_invoice" in text
        assert "75000" in text
        assert "exceeds approval threshold 50000" in text
        assert rows[0]["link"] in text
        # the run left waiting_approval and resumed normally
        assert handle.status != "waiting_input"
        assert final["report"]["status"] == "failed"  # fake graph's report
    finally:
        _cleanup(run_id)


def test_no_channels_means_no_notification_and_no_link(monkeypatch):
    run_id = "t_ch_no_channels"
    sid = _session()
    _insert(run_id, sid, "task")
    handle = runner.RunHandle(run_id=run_id, task="task", session_id=sid)
    handle.answers.put("y")
    runner._RUNS[run_id] = handle
    from ai_operator.tracing import TraceLogger

    try:
        runner._stream_loop(handle, _FakeGraph(_interrupt()), {}, {},
                            TraceLogger(run_id))
        assert db.list_notifications(run_id=run_id) == []
    finally:
        _cleanup(run_id)


# --- mandated test 2: expired and reused links are rejected ----------------


def test_expired_link_is_rejected():
    run_id = "t_ch_expired"
    sid = _session()
    _insert(run_id, sid, "task")
    token = channels.issue_link(run_id, ttl=-30)  # already expired
    try:
        resp = client.get(f"/approve/{token}")
        assert resp.status_code == 410
        assert "expired" in resp.text
    finally:
        _cleanup(run_id)


def test_reused_link_is_rejected():
    run_id = "t_ch_reused"
    sid = _session()
    _insert(run_id, sid, "task")
    token = channels.issue_link(run_id)
    try:
        first = client.get(f"/approve/{token}", follow_redirects=False)
        assert first.status_code == 303
        assert first.headers["location"] == f"/runs/{run_id}"
        second = client.get(f"/approve/{token}")
        assert second.status_code == 410
        assert "already used" in second.text
    finally:
        _cleanup(run_id)


def test_tampered_or_unknown_link_is_rejected():
    run_id = "t_ch_tampered"
    sid = _session()
    _insert(run_id, sid, "task")
    token = channels.issue_link(run_id)
    try:
        # flip the signature
        nonce, expiry, sig = token.split(".")
        bad = f"{nonce}.{expiry}.{('0' if sig[0] != '0' else '1')}{sig[1:]}"
        assert client.get(f"/approve/{bad}").status_code == 404
        assert client.get("/approve/not.a.token").status_code == 404
        assert client.get("/approve/unknownnonce.1.deadbeef").status_code == 404
    finally:
        _cleanup(run_id)


def test_link_does_not_bypass_role_check():
    """Opening the link only opens the card; approving enforces the role."""
    run_id = "t_ch_role_gate"
    sid = _session(role="ap_clerk")  # approve limit 25000
    _insert(run_id, sid, "pay 75000 to Acme")
    handle = runner.RunHandle(run_id=run_id, task="pay 75000 to Acme",
                              session_id=sid)
    handle.interrupt = _interrupt()
    handle.status = "waiting_input"
    runner._RUNS[run_id] = handle
    token = channels.issue_link(run_id)
    try:
        opened = client.get(f"/approve/{token}", follow_redirects=False)
        assert opened.status_code == 303  # card opens
        denied = client.post(f"/runs/{run_id}/answer",
                             data={"answer": "approve"})
        assert denied.status_code == 403
        assert "requires a higher role" in denied.text
        # the run never received the answer
        assert handle.status == "waiting_input"
        # ... and the denial is traced
        events = [json.loads(line) for line in
                  (RUNS_ROOT / run_id / "trace.jsonl").read_text().splitlines()
                  if line.strip()]
        assert any(e.get("event") == "approval_denied_role" for e in events)
    finally:
        _cleanup(run_id)


# --- alerts on bad endings --------------------------------------------------


def test_failed_and_interrupted_runs_alert(monkeypatch):
    monkeypatch.setattr(channels, "deliver_webhook",
                        lambda cfg, text, payload: None)
    sid = _session()
    for run_id, report_status, want_kind in (
        ("t_ch_fail", "failed", "failed"),
        ("t_ch_intr", "needs_human", "interrupted"),
    ):
        _insert(run_id, sid, "do the thing")
        _webhook_channel("https://hooks.example.invalid/x")
        from ai_operator.tracing import TraceLogger
        handle = runner.RunHandle(run_id=run_id, task="do the thing",
                                  session_id=sid)
        try:
            runner._finish(handle, TraceLogger(run_id),
                           {"report": {"status": report_status,
                                       "summary": "", "run_id": run_id,
                                       "task": "do the thing",
                                       "actions": [], "approvals": [],
                                       "evidence": [], "errors": []}})
            rows = db.list_notifications(run_id=run_id)
            assert len(rows) == 1
            assert rows[0]["kind"] == want_kind
            assert rows[0]["status"] == "sent"
            assert f"/runs/{run_id}" in rows[0]["link"]
            assert db.get_run(run_id)["state"] == (
                "failed" if report_status == "failed" else "interrupted")
        finally:
            _cleanup(run_id)
            with db.connect() as con:
                con.execute("DELETE FROM channels")


def test_delivery_failure_is_recorded_but_never_raises():
    run_id = "t_ch_deliv_fail"
    sid = _session()
    _insert(run_id, sid, "task")
    # unreachable port on localhost: connection refused inside the timeout
    _webhook_channel("http://127.0.0.1:1/hook")
    try:
        channels.on_state(run_id, "waiting_approval", interrupt=_interrupt())
        rows = db.list_notifications(run_id=run_id)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"
        assert rows[0]["error"]
        # the run state transition itself was untouched
        assert db.get_run(run_id)["state"] == "waiting_approval"
    finally:
        _cleanup(run_id)


# --- channels page + test button -------------------------------------------


def test_channels_page_renders_and_test_button_queues_one():
    resp = client.get("/channels")
    assert resp.status_code == 200
    assert "alert channels" in resp.text
    assert "No channels yet" in resp.text
    # invalid config is refused with a plain-English error
    bad = client.post("/channels", data={
        "name": "broken", "type": "webhook", "config": "not json"})
    assert "JSON object" in bad.text
    assert db.list_channels() == []
    # valid channel
    client.post("/channels", data={
        "name": "hook", "type": "webhook",
        "config": json.dumps({"url": "http://127.0.0.1:1/hook"}),
        "enabled": "on"})
    rows = db.list_channels()
    assert len(rows) == 1
    cid = rows[0]["id"]
    page = client.get("/channels").text
    assert "hook" in page and "webhook" in page
    # the test button queues exactly one notification (delivery fails fast)
    client.post(f"/channels/{cid}/test")
    notes = db.list_notifications()
    assert len(notes) == 1
    assert notes[0]["kind"] == "test"
    assert notes[0]["status"] == "failed"  # port 1 refuses; error recorded
    assert "Send test" in client.get("/channels").text
    # toggle and delete
    client.post(f"/channels/{cid}/toggle")
    assert db.get_channel(cid)["enabled"] in (0, False)
    client.post(f"/channels/{cid}/delete")
    assert db.list_channels() == []


def test_webhook_delivery_to_local_capture_server():
    """End-to-end webhook over loopback. Local capture = stub-level proof,
    NOT a real Slack/Teams endpoint."""
    received: list[bytes] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            received.append(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # noqa: ARG002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run_id = "t_ch_capture"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    _webhook_channel(f"http://127.0.0.1:{port}/hook")
    try:
        channels.on_state(run_id, "waiting_approval", interrupt=_interrupt())
        assert received, "webhook never reached the capture server"
        body = json.loads(received[0])
        assert "create_invoice" in body["text"]
        assert f"/approve/" in body["link"]
        assert body["run_id"] == run_id
        rows = db.list_notifications(run_id=run_id)
        assert len(rows) == 1 and rows[0]["status"] == "sent"
    finally:
        _cleanup(run_id)
        server.shutdown()
        server.server_close()
