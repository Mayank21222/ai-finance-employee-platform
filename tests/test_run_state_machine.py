"""Phase 3: run state machine, orphan reconcile, resume, history endpoints."""

import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_state.db"))

from fastapi.testclient import TestClient  # noqa: E402

from platform.dashboard import db, runner  # noqa: E402
from platform.dashboard.app import app  # noqa: E402

db.init_db()
client = TestClient(app)


def _insert(run_id: str, state: str, status: str = "running") -> None:
    db.record_run(run_id, 1, f"task for {run_id}")
    db.set_state(run_id, state)
    if status != "running":
        db.finish_run(run_id, status, state=state)


# --- state mapping ----------------------------------------------------------


def test_state_for_report_mapping():
    assert runner._state_for_report("verified_complete") == "completed"
    assert runner._state_for_report("needs_human") == "interrupted"
    assert runner._state_for_report("failed") == "failed"
    assert runner._state_for_report("anything") == "failed"


def test_finish_writes_state_into_db():
    _insert("t_state_finish", "running")
    handle = runner.RunHandle(run_id="t_state_finish", task="x", session_id=1)
    logger = type("L", (), {"write_report": lambda self, r: None})()
    runner._finish(handle, logger, {"report": {"status": "verified_complete"}})
    assert db.get_run("t_state_finish")["state"] == "completed"
    assert db.get_run("t_state_finish")["status"] == "verified_complete"

    _insert("t_state_noreport", "running")
    handle2 = runner.RunHandle(run_id="t_state_noreport", task="x",
                               session_id=1)
    runner._finish(handle2, logger, {})
    assert db.get_run("t_state_noreport")["state"] == "failed"


def test_db_lifecycle_queued_running_waiting_approval():
    _insert("t_state_lc", "queued")
    assert db.get_run("t_state_lc")["state"] == "queued"
    db.set_state("t_state_lc", "running")
    db.set_state("t_state_lc", "waiting_approval")
    assert db.get_run("t_state_lc")["state"] == "waiting_approval"
    orphaned = {r["run_id"] for r in db.orphaned_states()}
    assert "t_state_lc" not in orphaned  # waiting_approval is not orphaned
    db.set_state("t_state_lc", "running")
    orphaned = {r["run_id"] for r in db.orphaned_states()}
    assert "t_state_lc" in orphaned


# --- reconcile at startup ---------------------------------------------------


def test_reconcile_flips_orphans_but_keeps_waiting_approval():
    _insert("t_recon_running", "running")
    _insert("t_recon_queued", "queued")
    _insert("t_recon_wait", "waiting_approval")
    _insert("t_recon_done", "completed", status="verified_complete")
    try:
        flipped = runner.reconcile()
        assert "t_recon_running" in flipped
        assert "t_recon_queued" in flipped
        assert "t_recon_wait" not in flipped
        assert db.get_run("t_recon_running")["state"] == "interrupted"
        assert db.get_run("t_recon_wait")["state"] == "waiting_approval"
        assert db.get_run("t_recon_done")["state"] == "completed"
    finally:
        for rid in ("t_recon_running", "t_recon_queued", "t_recon_wait",
                    "t_recon_done"):
            with db.connect() as con:
                con.execute("DELETE FROM runs WHERE run_id = ?", (rid,))


# --- history / trace / evidence endpoints -----------------------------------


def test_history_page_and_trace_evidence_links():
    run_id = "t_hist_run"
    _insert(run_id, "completed", status="verified_complete")
    from ai_operator.tracing import RUNS_ROOT

    run_dir = RUNS_ROOT / run_id
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text(
        json.dumps({"event": "run_started", "run_id": run_id}) + "\n")
    (run_dir / "evidence" / "shot.png").write_bytes(b"png")
    try:
        page = client.get("/history")
        assert page.status_code == 200
        assert "Runs history" in page.text
        assert run_id in page.text
        assert ">completed<" in page.text
        assert f"/runs/{run_id}/trace" in page.text
        assert f"/runs/{run_id}/evidence" in page.text

        trace = client.get(f"/runs/{run_id}/trace")
        assert trace.status_code == 200
        assert "run_started" in trace.text

        ev = client.get(f"/runs/{run_id}/evidence")
        assert ev.status_code == 200
        assert "shot.png" in ev.text

        # single-file evidence endpoint still works
        one = client.get(f"/runs/{run_id}/evidence/shot.png")
        assert one.status_code == 200
    finally:
        import shutil

        shutil.rmtree(run_dir, ignore_errors=True)
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def test_missing_trace_returns_404():
    resp = client.get("/runs/t_no_trace/trace")
    assert resp.status_code == 404


# --- resume from checkpoint -------------------------------------------------


def test_orphaned_waiting_approval_shows_resume_form():
    run_id = "t_resume_form"
    _insert(run_id, "waiting_approval")
    from ai_operator.tracing import RUNS_ROOT

    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.jsonl").write_text("\n".join([
        json.dumps({"event": "run_started"}),
        json.dumps({"event": "approval_requested",
                    "question": "Approve payment of 42500?"}),
    ]))
    try:
        page = client.get(f"/runs/{run_id}")
        assert page.status_code == 200
        assert 'id="resume-panel"' in page.text
        assert "Approve payment of 42500?" in page.text
        assert 'action="/runs/%s/resume"' % run_id in page.text
        assert 'id="state-badge"' in page.text
        assert ">waiting_approval<" in page.text
        # the live answer form stays hidden (waiting=False); resume replaces it
        assert 'id="answer-panel" style="display:none"' in page.text
    finally:
        import shutil

        shutil.rmtree(run_dir, ignore_errors=True)
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def test_resume_endpoint_spawns_worker_with_answer(monkeypatch):
    run_id = "t_resume_call"
    _insert(run_id, "waiting_approval")
    captured = {}

    def fake_worker(handle, session, answer):
        captured["run_id"] = handle.run_id
        captured["answer"] = answer
        handle.status = "done"

    monkeypatch.setattr(runner, "_resume_worker", fake_worker)
    try:
        resp = client.post(f"/runs/{run_id}/resume",
                           data={"answer": "y - approved"}, follow_redirects=False)
        assert resp.status_code == 303
        deadline = time.time() + 5
        while "answer" not in captured and time.time() < deadline:
            time.sleep(0.05)
        assert captured == {"run_id": run_id, "answer": "y - approved"}

        # waiting_approval -> running as soon as the resume thread starts
        db.set_state(run_id, "waiting_approval")  # reset what worker may touch
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
    finally:
        runner._RUNS.pop(run_id, None)
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))


def test_resume_rejected_for_non_waiting_run():
    _insert("t_resume_bad", "completed", status="verified_complete")
    try:
        resp = client.post("/runs/t_resume_bad/resume", data={"answer": "y"},
                           follow_redirects=False)
        assert resp.status_code == 409
        assert "not waiting for approval" in resp.text
    finally:
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?", ("t_resume_bad",))


def test_run_console_shows_state_column():
    _insert("t_console_state", "running")
    try:
        page = client.get("/runs").text
        assert ">State<" in page
        assert "t_console_state" in page
        assert 'class="badge running"' in page
    finally:
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE run_id = ?",
                        ("t_console_state",))
