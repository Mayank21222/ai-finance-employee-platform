"""Phase 5 additions: agent version history, token/cost tracking, schedules,
reusable skills, prompt testing panel."""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

os.environ.setdefault("DASH_DB",
                      os.path.join(tempfile.mkdtemp(prefix="phase5_dash_"),
                                   "dashboard.db"))
os.environ["STUB_MODEL"] = "1"

from ai_operator.graph import DEFAULT_FLOW_PATH  # noqa: E402
from ai_operator.llm import (estimated_cost_usd, get_client,  # noqa: E402
                             take_usage)
from platform.dashboard import db, runner, scheduler  # noqa: E402
from platform.dashboard import app as dashapp  # noqa: E402

RUNS_ROOT = dashapp.RUNS_ROOT
client = TestClient(dashapp.app)
db.init_db()


# --- P5-1 agent configuration version history -------------------------------


def test_agent_version_history_records_saves_and_restore():
    original = DEFAULT_FLOW_PATH.read_text(encoding="utf-8")
    node_id = "ver_agent"
    try:
        resp = client.post("/agents", data={"node_id": node_id,
                                            "next_node_ids": "report"},
                           follow_redirects=False)
        assert resp.status_code == 303, resp.text
        # Two panel saves with different instructions.
        assert client.post(f"/flow/nodes/{node_id}",
                           data={"instructions": "Step 1: alpha."}
                           ).status_code == 200
        assert client.post(f"/flow/nodes/{node_id}",
                           data={"instructions": "Step 1: beta."}
                           ).status_code == 200

        rows = db.list_versions(node_id)
        assert [r["version"] for r in rows] == [2, 1]
        # v1 kept the configuration as created; v2 kept what save #1 replaced.
        assert rows[1]["config"]["instructions"] == ""
        assert rows[0]["config"]["instructions"] == "Step 1: alpha."

        # The versions tab lists them and names the changed field.
        panel = client.get(f"/flow/nodes/{node_id}/versions")
        assert panel.status_code == 200
        assert "v1" in panel.text and "instructions" in panel.text
        # The compare view shows the live config beside v1.
        cmp_resp = client.get(f"/flow/nodes/{node_id}/versions/1")
        assert cmp_resp.status_code == 200
        assert "Step 1: beta." in cmp_resp.text
        # The edit panel exposes the versions tab.
        assert "/flow/nodes/ver_agent/versions" in \
            client.get(f"/flow/nodes/{node_id}").text

        # Restore v1: v3 records the restore, the live config matches v1.
        resp = client.post(f"/flow/nodes/{node_id}/versions/1/restore",
                           follow_redirects=False)
        assert resp.status_code == 200
        cfg = json.loads(DEFAULT_FLOW_PATH.read_text(encoding="utf-8"))
        node = next(n for n in cfg["nodes"] if n["node_id"] == node_id)
        rows = db.list_versions(node_id)
        assert [r["version"] for r in rows] == [3, 2, 1]
        assert "restore" in rows[0]["label"]
        assert rows[0]["config"]["instructions"] == "Step 1: beta."
        assert node["instructions"] == rows[2]["config"]["instructions"] == ""
        # Unknown versions 404 without touching the config.
        assert client.get(
            f"/flow/nodes/{node_id}/versions/99").status_code == 404
    finally:
        if DEFAULT_FLOW_PATH.read_text(encoding="utf-8") != original:
            DEFAULT_FLOW_PATH.write_text(original, encoding="utf-8")


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


def _run_mini_flow(tmp_path, name: str) -> str:
    """Start MIN_AGENT_FLOW (one stub classification -> end) and wait for it."""
    flow_path = tmp_path / name
    flow_path.write_text(json.dumps(MIN_AGENT_FLOW), encoding="utf-8")
    os.environ["COMP_OPS_FLOW"] = str(flow_path)
    os.environ["COMP_OPS_SKIP_APP"] = "1"
    resp = client.post("/runs", data={"session_id": "1", "task": "say hi"},
                       follow_redirects=False)
    assert resp.status_code == 303, resp.text
    run_id = resp.headers["location"].rsplit("/", 1)[-1]
    deadline = time.time() + 30
    while time.time() < deadline:
        handle = runner.get(run_id)
        if handle is not None and handle.terminal:
            return run_id
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_run_records_agent_versions_used(tmp_path):
    try:
        expected = len(db.list_versions("cls")) + 1
        run_id = _run_mini_flow(tmp_path, "ver_flow.json")
        row = db.get_run(run_id)
        assert json.loads(row["agent_versions_used"]) == {"cls": expected}
        # The history page shows which version the run used.
        assert f"cls v{expected}" in client.get("/history").text
    finally:
        os.environ.pop("COMP_OPS_FLOW", None)
        os.environ.pop("COMP_OPS_SKIP_APP", None)


# --- P5-2 token and cost tracking -------------------------------------------


def test_estimated_cost_usd_math_and_usage_reset():
    # Claude Sonnet 4.6: $3 / 1M input tokens, $15 / 1M output tokens.
    assert estimated_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0
    assert estimated_cost_usd("stub", 250, 180) == 0.0  # unknown model -> free
    stub = get_client()  # STUB_MODEL=1 in this module
    stub.complete("system", "user")
    usage = take_usage(stub)
    assert usage["input_tokens"] == 250 and usage["output_tokens"] == 180
    assert take_usage(stub) is None  # cleared: a retry can never double-count


def test_run_records_token_usage_in_report_and_history(tmp_path):
    try:
        run_id = _run_mini_flow(tmp_path, "tok_flow.json")
        report = json.loads(
            (RUNS_ROOT / run_id / "report.json").read_text(encoding="utf-8"))
        tok = report["tokens"]
        assert tok["input"] >= 250 and tok["output"] >= 180
        assert tok["total"] == tok["input"] + tok["output"]
        assert tok["model"] == "stub"
        # The trace records one model_call event per model call.
        events = [
            json.loads(line)
            for line in (RUNS_ROOT / run_id / "trace.jsonl").read_text(
                encoding="utf-8").splitlines() if line.strip()
        ]
        calls = [e for e in events if e.get("event") == "model_call"]
        assert calls and all(e["estimated_cost_usd"] == 0.0 for e in calls)
        # History page shows the token cell for this run.
        assert f'{tok["input"]}/{tok["output"]}' in client.get("/history").text
    finally:
        os.environ.pop("COMP_OPS_FLOW", None)
        os.environ.pop("COMP_OPS_SKIP_APP", None)


# --- P5-3 scheduled background runs -----------------------------------------


def test_schedule_parse_and_due_window():
    assert scheduler.parse_cron("daily 09:30") == ("daily", None, 9, 30)
    assert scheduler.parse_cron("weekly mon 09:30") == ("weekly", 0, 9, 30)
    assert scheduler.parse_cron("every 5 min") is None
    assert scheduler.parse_cron("daily 99:00") is None
    sched = {"cron": "daily 09:30", "tz_offset": 0, "last_run_at": None}
    due = datetime(2026, 1, 5, 9, 30, 20, tzinfo=timezone.utc).timestamp()
    late = datetime(2026, 1, 5, 9, 31, 20, tzinfo=timezone.utc).timestamp()
    assert scheduler.is_due(sched, due)
    assert not scheduler.is_due(sched, late)  # outside the minute window
    # weekly only on the named day (2026-01-05 is a Monday).
    weekly = {"cron": "weekly tue 09:30", "tz_offset": 0, "last_run_at": None}
    assert not scheduler.is_due(weekly, due)


def test_due_schedule_fires_a_run_with_source_schedule():
    sid = db.add_schedule(session_id=1, task="scheduled report",
                          cron="daily 09:30", tz_offset=0.0)
    run_id = "run_test_sched_fire"
    try:
        def fake_start(task, session_id, source="manual", schedule_id=None):
            db.record_run(run_id, session_id, task, source=source,
                          schedule_id=schedule_id)
            return SimpleNamespace(run_id=run_id)

        due = datetime(2026, 1, 5, 9, 30, 10, tzinfo=timezone.utc).timestamp()
        fired = scheduler.check_due(now=due, start=fake_start)
        assert run_id in fired
        row = db.get_run(run_id)
        assert row["source"] == "schedule"
        assert row["schedule_id"] == sid
        assert db.get_schedule(sid)["last_run_id"] == run_id
    finally:
        db.delete_schedule(sid)


def test_due_schedule_with_active_run_is_recorded_skipped(monkeypatch):
    sid = db.add_schedule(session_id=1, task="scheduled report",
                          cron="daily 10:15", tz_offset=0.0)
    try:
        monkeypatch.setattr(scheduler.runner, "active",
                            lambda: SimpleNamespace(run_id="busy"))
        called = []
        due = datetime(2026, 1, 5, 10, 15, 5, tzinfo=timezone.utc).timestamp()
        fired = scheduler.check_due(
            now=due, start=lambda *a, **k: called.append(a) or None)
        assert called == []  # never started while another run was active
        assert fired, "the skip should still be reported"
        skipped = db.get_run(fired[0])
        assert skipped["state"] == "skipped"
        assert skipped["source"] == "schedule"
        assert skipped["schedule_id"] == sid
        assert "active" in (skipped["skipped_reason"] or "")
    finally:
        db.delete_schedule(sid)