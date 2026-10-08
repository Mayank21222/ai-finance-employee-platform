"""Phase 6 section 3: the `fin` CLI.

Mandated coverage:
  * `fin flow validate` exits non-zero on the broken fixture and zero on
    `configs/finance_employee.json`;
  * `fin flow import` refuses invalid configs and produces agent version
    rows on a round trip.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED = _REPO_ROOT / "configs" / "finance_employee.json"

BROKEN_FLOW = {
    "start_node_id": "a",
    "session_context": {"tenant": "acme", "currency": "INR",
                        "approval_threshold": 50000.0,
                        "user_role": "finance_operator"},
    "nodes": [{"type": "agent", "node_id": "a",
               "next_node_ids": ["ghost"]}],
}


def _fin(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "platform.cli", *args],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60)


def test_flow_validate_exit_codes(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(BROKEN_FLOW), encoding="utf-8")
    proc = _fin("flow", "validate", str(broken))
    assert proc.returncode != 0, proc.stdout
    assert "ERROR:" in proc.stdout
    assert "ghost" in proc.stdout and "end node" in proc.stdout
    # blocking errors -> INVALID
    assert "INVALID" in proc.stdout

    proc = _fin("flow", "validate", str(SHIPPED))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VALID" in proc.stdout
    assert "guardrail score" in proc.stdout  # warnings are reported, not fatal


def test_flow_import_round_trip_creates_version_rows(tmp_path):
    from platform.dashboard import db

    db.init_db()
    original = SHIPPED.read_text(encoding="utf-8")
    try:
        cfg = json.loads(original)
        ap = next(n for n in cfg["nodes"] if n["node_id"] == "ap_agent")
        ap["instructions"] = ap["instructions"] + "\nCLI import edit."
        imported = tmp_path / "imported.json"
        imported.write_text(json.dumps(cfg), encoding="utf-8")

        before = len(db.list_versions("ap_agent"))
        proc = _fin("flow", "import", str(imported))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "ap_agent" in proc.stdout
        # semantic round trip: both files normalize to the same flow
        from platform.flow.models import Flow

        installed = Flow.model_validate(
            json.loads(SHIPPED.read_text(encoding="utf-8")))
        wanted = Flow.model_validate(
            json.loads(imported.read_text(encoding="utf-8")))
        assert installed.model_dump(mode="json") == wanted.model_dump(
            mode="json")

        versions = db.list_versions("ap_agent")
        assert len(versions) == before + 1
        assert versions[0]["label"] == "flow import"
        # the row snapshots the configuration the import replaced
        assert versions[0]["config"]["instructions"].endswith(
            "7. Hand off to verification and act on its result.")
        assert "CLI import edit." not in versions[0]["config"]["instructions"]

        # an invalid file is refused, exit non-zero, target untouched
        broken = tmp_path / "broken.json"
        broken.write_text(json.dumps(BROKEN_FLOW), encoding="utf-8")
        current = SHIPPED.read_text(encoding="utf-8")
        proc = _fin("flow", "import", str(broken))
        assert proc.returncode != 0, proc.stdout
        assert "REFUSED" in proc.stdout
        assert SHIPPED.read_text(encoding="utf-8") == current
        # nothing imported -> no extra version row beyond the first import
        assert len(db.list_versions("ap_agent")) == before + 1
    finally:
        if SHIPPED.read_text(encoding="utf-8") != original:
            SHIPPED.write_text(original, encoding="utf-8")


def test_init_runs_list_and_approvals_list():
    proc = _fin("init")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "flow config" in proc.stdout and "roles:" in proc.stdout

    proc = _fin("runs", "list")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "run_id" in proc.stdout or "no runs yet" in proc.stdout

    proc = _fin("approvals", "list")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no pending approvals" in proc.stdout or "reason:" in proc.stdout


def test_fin_launcher_script_exists_and_is_executable():
    launcher = _REPO_ROOT / "fin"
    assert launcher.is_file()
    assert os.access(launcher, os.X_OK), "fin must be executable"
