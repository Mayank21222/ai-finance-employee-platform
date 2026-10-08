"""Phase 6 additions: roles, the permissions matrix, and role-based gates.

Mandated coverage (Improvements.md Section 1):
  * a read-only role cannot call a write tool, blocked in code + traced;
  * the approval gate respects each role's approve limit (ap_clerk capped,
    finance_manager unlimited), and the dashboard answer endpoint refuses an
    approval with a higher-role denial (status 403, never a 200);
  * an approval card rendered for an insufficient role shows the reason and
    hides the Approve button;
  * the flow validator rejects an agent that declares a role with no row in
    the roles table.

This module owns its own temp DB (like test_phase4/test_dashboard) so the
roles matrix toggles and per-role sessions never touch other test modules.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

os.environ["DASH_DB"] = str(Path(tempfile.mkdtemp()) / "phase6.db")

from fastapi.testclient import TestClient  # noqa: E402

from ai_operator.state import INITIAL_STATE_KEYS  # noqa: E402
from ai_operator.tracing import RUNS_ROOT, TraceLogger, unregister  # noqa: E402
from platform.dashboard import db, runner  # noqa: E402
from platform.dashboard.app import app  # noqa: E402
from platform.flow import compiler  # noqa: E402
from platform.flow.models import Flow  # noqa: E402
from platform.flow.validator import validate  # noqa: E402

db.init_db()
client = TestClient(app)


# --- helpers ----------------------------------------------------------------


def _event(ev: str, **kw) -> dict:
    return {"time": "2026-03-01T10:00:00", **kw, "event": ev}


def _trace(run_id: str, events: list[dict]) -> Path:
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n")
    return run_dir


def _insert(run_id: str, sid: int, task: str) -> None:
    db.record_run(run_id, sid, task)
    db.set_state(run_id, "waiting_approval")


def _cleanup(run_id: str) -> None:
    unregister(run_id)
    shutil.rmtree(RUNS_ROOT / run_id, ignore_errors=True)
    runner._RUNS.pop(run_id, None)
    with db.connect() as con:
        con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def _session(user_role: str) -> int:
    for s in db.list_sessions():
        if s["user_role"] == user_role:
            return s["id"]
    return db.add_session(f"tenant_{user_role}", "INR", 50000.0, user_role)


def _approval_interrupt(amount: str = "75000") -> dict:
    return {
        "kind": "approval", "tool_name": "create_invoice",
        "tool_args": {"amount": amount},
        "reason": f"amount {amount} exceeds approval threshold 50000",
        "question": f"Create invoice for {amount}?",
    }


def _live(handle: runner.RunHandle, interrupt: dict) -> None:
    handle.status = "waiting_input"
    handle.interrupt = interrupt
    runner._RUNS[handle.run_id] = handle


class ToolThenFinishClient:
    def __init__(self, tool: str, args: dict):
        self.decisions = 0
        self.tool = tool
        self.args = args

    def complete(self, system: str, user: str) -> str:
        if '"understanding"' in system:
            return json.dumps({"understanding": "u", "plan": "p"})
        self.decisions += 1
        if self.decisions == 1:
            return json.dumps({
                "thought": "calling a tool", "action_type": "tool_call",
                "tool_name": self.tool, "tool_args": self.args,
                "plan_update": None, "expected_outcome": "ok",
            })
        return json.dumps({
            "thought": "done", "action_type": "finish", "tool_name": None,
            "tool_args": {}, "plan_update": None, "expected_outcome": "ok",
        })


def _flow_with_role(role: str, node_id: str, tools: list[str]) -> Flow:
    return Flow.model_validate({
        "start_node_id": node_id,
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator",
                            "tools_enabled": []},
        "nodes": [
            {"type": "agent", "node_id": node_id, "role": role,
             "instructions": "please make one tool call then finish",
             "tools_enabled": tools, "next_node_ids": ["finish"],
             "fallback_next": "finish"},
            {"type": "end", "node_id": "finish"},
        ],
    })


# --- P6-1 the read-only role cannot call a write tool -----------------------


def test_read_only_role_cannot_call_write_tool():
    from ai_operator import roles

    # A role with tool:* read (auditor) must be blocked from write_memory in
    # code, trailing a permission_denied_role trace event, and must never run
    # the tool (no variable, no history ok=True).
    assert roles.tool_gate("auditor", "write_memory", "write") is not None
    assert roles.tool_gate("finance_operator", "write_memory", "write") is None

    run_id = "p6_cant_write"
    logger = TraceLogger(run_id)
    try:
        flow = _flow_with_role("auditor", "p6_auditor", ["write_memory"])
        final = compiler.compile_flow(flow, ToolThenFinishClient(
            "write_memory", {"key": "memo", "value": "leak"})).compile().invoke({
                "task": "persist a memo",
                "run_id": run_id,
                **INITIAL_STATE_KEYS,
            })
        history = final["history"]
        denial = [h for h in history
                  if h.get("kind") == "error" and "blocked tool" in h.get("detail", "")]
        assert denial, history
        assert "auditor" in denial[0]["detail"]
        assert final["variables"].get("memo") is None
        events = [e for e in logger.events()
                  if e["event"] == "permission_denied_role"]
        assert events, logger.events()
        assert events[0]["tool_name"] == "write_memory"
        assert events[0]["role"] == "auditor"
        assert "read" in events[0]["reason"]
    finally:
        _cleanup(run_id)


# --- P6-1 the approval gate honours each role's limit -----------------------


def test_approval_gate_respects_role_limits():
    from ai_operator import roles

    # ap_clerk: capped at 25000 (Improvements.md), so 75000 is refused.
    ok, reason = roles.approve_gate("ap_clerk", 75000)
    assert not ok and "approve up to 25,000" in reason, reason
    ok, _ = roles.approve_gate("ap_clerk", 15000)
    assert ok
    # auditor can never approve.
    ok, reason = roles.approve_gate("auditor", 1)
    assert not ok
    # finance_manager approves without any cap.
    for amount in (75000, 10_000_000):
        ok, _ = roles.approve_gate("finance_manager", amount)
    assert ok
    # finance_operator (the seeded session's role) must clear 75000.
    ok, _ = roles.approve_gate("finance_operator", 75000)
    assert ok


# --- P6-1 the approval card and answer endpoint enforce the role ------------


def _run_denial_for_role(user_role: str) -> tuple[str, Path]:
    run_id = f"p6_deny_{user_role}"
    sid = _session(user_role)
    _insert(run_id, sid, "pay 75000")
    trace_dir = _trace(run_id, [
        _event("run_started", task="pay 75000"),
        _event("approval_requested", kind="approval",
               tool_name="create_invoice",
               tool_args={"amount": "75000"},
               reason="amount 75000 exceeds approval threshold 50000",
               question="Create invoice for 75000?"),
    ])
    _live(runner.RunHandle(run_id=run_id, task="pay 75000", session_id=sid),
          _approval_interrupt())
    return run_id, trace_dir


def test_approval_card_and_endpoint_deny_insufficient_role():
    from ai_operator import roles

    run_id, ctx = _run_denial_for_role("ap_clerk")
    try:
        # Read-only auditor cannot approve at all; ap_clerk cannot approve 75000.
        assert not roles.approve_gate("ap_clerk", 75000)[0]

        # Card: reason shown, Approve hidden (no approve hx-vals on the card).
        body = client.get(f"/runs/{run_id}").text
        assert "requires a higher role" in body
        assert 'hx-vals=\'{"answer": "approve"}\'' not in body

        # Answer endpoint: refusal is a real code-level denial - a 403, never
        # a 200 approval, and the run stays waiting.
        resp = client.post(f"/runs/{run_id}/answer", data={"answer": "approve"})
        assert resp.status_code == 403, resp.status_code
        assert "requires a higher role" in resp.text
        assert not resp.text.startswith("<div")  # not a re-rendered card
        assert db.get_run(run_id)["state"] == "waiting_approval"

        # The refusal is traced as approval_denied_role with role + amount.
        events = [json.loads(l) for l in
                  (RUNS_ROOT / run_id / "trace.jsonl").read_text().splitlines()]
        denial = [e for e in events if e["event"] == "approval_denied_role"]
        assert denial
        assert denial[0]["role"] == "ap_clerk"
        assert denial[0]["amount"] == 75000
        assert "approve up to" in denial[0]["reason"]
    finally:
        _cleanup(run_id)


def test_approval_answer_allowed_for_audit_or_unlimited_role():
    # finance_manager (no limit) sails through the same endpoint: the answer
    # is accepted with a 200 card re-render exactly as pre-roles behaviour.
    run_id, ctx = _run_denial_for_role("finance_manager")
    try:
        resp = client.post(f"/runs/{run_id}/answer", data={"answer": "approve"})
        assert resp.status_code == 200, resp.status_code
    finally:
        _cleanup(run_id)


# --- P6-1 the validator rejects a declared-but-unknown role -----------------


def test_validator_rejects_agent_with_undeclared_role():
    flow = _flow_with_role("ceo", "p6_ceo", [])
    errs = [str(e) for e in validate(flow)]
    role_errs = [e for e in errs if "role" in e and "does not exist" in e]
    assert role_errs, errs
    assert "p6_ceo" in role_errs[0] and "'ceo'" in role_errs[0]

    # Roles returned by the platform are valid, so the same flow passes with
    # a real seeded role, and the roles page exposes them for session selects.
    assert _flow_with_role("ap_clerk", "p6_clerk", []) and validate(
        _flow_with_role("ap_clerk", "p6_clerk", [])) == []
    assert {r["name"] for r in db.list_roles()} >= {
        "finance_manager", "ap_clerk", "auditor", "finance_operator", "viewer"}


# --- P6-1 the matrix toggle writes permissions and the audit trail ----------


def test_matrix_toggle_writes_permission_and_audit_event():
    resp = client.get("/roles")
    assert resp.status_code == 200
    assert "Permissions matrix" in resp.text
    assert "action:approve_payment" in resp.text
    assert "/roles/" in resp.text  # per-row toggle buttons
    # The roles page is a NAV destination in the shell.
    assert 'href="/roles"' in client.get("/").text

    viewer = next(r for r in db.list_roles() if r["name"] == "viewer")
    resp = client.post(f"/roles/{viewer['id']}/permissions",
                       data={"resource": "tool:write_memory"},
                       follow_redirects=False)
    assert resp.status_code == 303, resp.status_code
    # viewer's read-only tool:* wildcard stays; the new row is the exact grant
    # and the matrix page renders it, and the toggle landed in the DB.
    row = db.role_permission_map().get("viewer", {}).get("tool:write_memory")
    assert row and row[0] == "read", row
    # The audit trail carries role_permission_changed with before/after.
    events = []
    cfg_trace = RUNS_ROOT / "_config" / "trace.jsonl"
    events = [json.loads(l) for l in cfg_trace.read_text().splitlines()]
    changed = [e for e in events if e["event"] == "role_permission_changed"]
    assert changed
    assert changed[-1]["resource"] == "tool:write_memory"
    assert changed[-1]["old"] == "none" and changed[-1]["new"] == "read"