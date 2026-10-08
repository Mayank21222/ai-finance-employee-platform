"""Phase 5 additions: agent version history, token/cost tracking, schedules,
reusable skills, prompt testing panel."""

import json
import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("DASH_DB",
                      os.path.join(tempfile.mkdtemp(prefix="phase5_dash_"),
                                   "dashboard.db"))
os.environ["STUB_MODEL"] = "1"

from ai_operator.graph import DEFAULT_FLOW_PATH  # noqa: E402
from platform.dashboard import db, runner  # noqa: E402
from platform.dashboard import app as dashapp  # noqa: E402

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


def test_run_records_agent_versions_used(tmp_path):
    flow_path = tmp_path / "ver_flow.json"
    flow_path.write_text(json.dumps(MIN_AGENT_FLOW), encoding="utf-8")
    os.environ["COMP_OPS_FLOW"] = str(flow_path)
    os.environ["COMP_OPS_SKIP_APP"] = "1"
    try:
        expected = len(db.list_versions("cls")) + 1
        resp = client.post("/runs", data={"session_id": "1", "task": "say hi"},
                           follow_redirects=False)
        assert resp.status_code == 303
        run_id = resp.headers["location"].rsplit("/", 1)[-1]
        deadline = time.time() + 30
        while time.time() < deadline:
            handle = runner.get(run_id)
            if handle is not None and handle.terminal:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("run did not finish")
        row = db.get_run(run_id)
        assert json.loads(row["agent_versions_used"]) == {"cls": expected}
        # The history page shows which version the run used.
        assert f"cls v{expected}" in client.get("/history").text
    finally:
        os.environ.pop("COMP_OPS_FLOW", None)
        os.environ.pop("COMP_OPS_SKIP_APP", None)