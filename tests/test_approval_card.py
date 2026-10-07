"""Phase 3: approval renders as a decision card, not a text prompt."""

import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_state.db"))

from fastapi.testclient import TestClient  # noqa: E402

from ai_operator.tracing import RUNS_ROOT  # noqa: E402
from platform.dashboard import db, runner  # noqa: E402
from platform.dashboard.app import app  # noqa: E402

db.init_db()
client = TestClient(app)


def _session() -> int:
    # the seeded acme session (id 1) carries user_role finance_operator;
    # reuse it so the shared test DB stays untouched for other modules.
    for s in db.list_sessions():
        if s["tenant"] == "acme":
            return s["id"]
    raise AssertionError("seeded acme session missing")


def _event(ev: str, **kw) -> dict:
    return {"time": "2026-03-01T10:00:00", **kw, "event": ev}


def _trace(run_id: str, events: list[dict]) -> Path:
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events))
    return run_dir


def _insert(run_id: str, sid: int, task: str) -> None:
    db.record_run(run_id, sid, task)
    db.set_state(run_id, "waiting_approval")


def _cleanup(run_id: str) -> None:
    shutil.rmtree(RUNS_ROOT / run_id, ignore_errors=True)
    runner._RUNS.pop(run_id, None)
    with db.connect() as con:
        con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def _approval_trace(run_id: str, decided: dict | None = None) -> Path:
    events = [
        _event("run_started", task="pay 75000 to Acme"),
        _event("node_entered", node="ap_agent", node_type="agent"),
        _event("tool_result", tool="browse_invoices", args={}, ok=True),
        _event("decision", action_type="tool_call", thought="need to pay"),
        _event("approval_requested", kind="approval",
               tool_name="create_invoice",
               tool_args={"amount": "75000", "vendor": "acme"},
               reason="amount 75000 exceeds approval threshold 50000",
               question="Create invoice for 75000?"),
    ]
    if decided is not None:
        events.append(_event("human_input", kind="approval", **decided))
    return _trace(run_id, events)


def _live(handle: runner.RunHandle, interrupt: dict) -> None:
    handle.status = "waiting_input"
    handle.interrupt = interrupt
    runner._RUNS[handle.run_id] = handle


def _approval_interrupt() -> dict:
    return {
        "kind": "approval", "tool_name": "create_invoice",
        "tool_args": {"amount": "75000"},
        "reason": "amount 75000 exceeds approval threshold 50000",
        "question": "Create invoice for 75000?",
    }


def test_approval_card_shows_action_policy_summary_and_buttons():
    run_id = "t_card_pending"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    _approval_trace(run_id)
    _live(runner.RunHandle(run_id=run_id, task="x", session_id=sid),
          _approval_interrupt())
    try:
        body = client.get(f"/runs/{run_id}").text
        assert 'id="approval-panel"' in body
        assert "approval requested" in body
        assert "create_invoice" in body          # the requested tool
        assert "75000" in body                    # the requested data
        assert ("policy rule: <strong>amount 75000 "
                "exceeds approval threshold 50000</strong>") in body
        assert "browse_invoices" in body          # done-so-far summary
        assert "done so far" in body
        # Approve/Reject are hx-post buttons to the existing answer endpoint
        assert f"hx-post=\"/runs/{run_id}/answer\"" in body
        assert 'hx-vals=\'{"answer": "approve"}\'' in body
        assert 'hx-vals=\'{"answer": "reject"}\'' in body
        # the free-text input stays hidden for an approval
        assert 'id="answer-panel" style="display:none"' in body
    finally:
        _cleanup(run_id)


def test_answered_card_stays_in_trace_with_verdict_and_approver():
    run_id = "t_card_decided"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    _approval_trace(run_id, decided={"answer": "approve"})
    try:
        body = client.get(f"/runs/{run_id}").text
        assert "approval approved" in body
        assert "Approved by finance_operator" in body
        # the card persists: action and policy rule stay visible
        assert "create_invoice" in body
        assert "policy rule:" in body
        # no answer buttons remain once decided (tool toggles still use hx-post)
        assert f"hx-post=\"/runs/{run_id}/answer\"" not in body
    finally:
        _cleanup(run_id)


def test_rejected_card_shows_rejection():
    run_id = "t_card_rejected"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    _approval_trace(run_id, decided={"answer": "reject"})
    try:
        body = client.get(f"/runs/{run_id}").text
        assert "approval rejected" in body
        assert "Rejected by finance_operator" in body
    finally:
        _cleanup(run_id)


def test_clarification_keeps_text_form_not_card():
    run_id = "t_card_clarify"
    sid = _session()
    _insert(run_id, sid, "ask a question")
    _trace(run_id, [
        _event("run_started"),
        _event("human_input_requested", kind="clarification",
               question="Which vendor?"),
    ])
    _live(runner.RunHandle(run_id=run_id, task="x", session_id=sid),
          {"kind": "clarification", "question": "Which vendor?"})
    try:
        body = client.get(f"/runs/{run_id}").text
        assert 'id="answer-panel"' in body
        assert 'id="answer-panel" style="display:none"' not in body
        assert "Which vendor?" in body
        assert '<input name="answer"' in body
        # no decision card (the panel body is empty until an approval fires)
        assert "approval requested" not in body
    finally:
        _cleanup(run_id)


def test_htmx_answer_returns_decided_card_in_place():
    run_id = "t_card_hx"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    run_dir = _approval_trace(run_id)
    _live(runner.RunHandle(run_id=run_id, task="x", session_id=sid),
          _approval_interrupt())
    # the worker records the decision, then the HX round-trip re-renders it
    with open(run_dir / "trace.jsonl", "a") as fh:
        fh.write("\n" + json.dumps(_event("human_input", kind="approval",
                                          answer="approve")) + "\n")
    try:
        resp = client.post(f"/runs/{run_id}/answer",
                           data={"answer": "approve"},
                           headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert resp.text.startswith("<div")
        assert "approval approved" in resp.text
        assert "Approved by finance_operator" in resp.text
        assert "create_invoice" in resp.text
    finally:
        _cleanup(run_id)


def test_approval_card_fragment_endpoint():
    run_id = "t_card_frag"
    sid = _session()
    _insert(run_id, sid, "pay 75000 to Acme")
    _approval_trace(run_id)
    try:
        resp = client.get(f"/runs/{run_id}/approval-card")
        assert resp.status_code == 200
        assert "approval requested" in resp.text
        assert "create_invoice" in resp.text
    finally:
        _cleanup(run_id)