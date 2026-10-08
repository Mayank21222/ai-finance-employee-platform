"""Phase 6 section 4: AI-assisted flow drafting with a human gate.

Offline by construction: STUB_MODEL=1 (tests/conftest.py) returns the canned
two-agent draft, so no API key and no real model call is involved anywhere
in this file.
"""

import copy
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_draft.db"))

import pytest  # noqa: E402

from platform.dashboard import drafting, flowcfg  # noqa: E402
from platform.dashboard import db  # noqa: E402
from platform.dashboard.app import app  # noqa: E402
from platform.flow.models import Flow  # noqa: E402
from platform.flow.validator import validate  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

db.init_db()
client = TestClient(app)

CONFIG_PATH = Path(flowcfg.DEFAULT_FLOW_PATH)


@pytest.fixture()
def config_bytes():
    """The shipped config must survive every test byte-for-byte."""
    before = CONFIG_PATH.read_bytes()
    yield
    CONFIG_PATH.write_bytes(before)


@pytest.fixture()
def no_ai_draft_versions():
    yield
    with db.connect() as con:
        con.execute("DELETE FROM agent_versions WHERE label = 'AI draft'")


def _minimal_flow(extra_agent: dict | None = None) -> dict:
    nodes = [
        {"type": "agent", "node_id": "a",
         "next_node_ids": ["missing_node"], "fallback_next": None,
         "tools_enabled": []},
        {"type": "end", "node_id": "end_node"},
    ]
    if extra_agent:
        nodes.insert(1, extra_agent)
    return {
        "name": "f", "start_node_id": "a",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000,
                            "user_role": "finance_operator"},
        "nodes": nodes,
    }


# --- stub draft validates and renders a diff --------------------------------


def test_stub_draft_validates_and_renders_diff(config_bytes):
    resp = client.post("/flow/draft",
                       data={"description": "a two-person AP team"})
    assert resp.status_code == 200
    body = resp.text
    assert "draft ready" in body              # preview banner
    assert "nothing is saved" in body.lower() or "preview only" in body
    assert "Diff against the current flow" in body
    assert "intake_agent" in body and "specialist_agent" in body
    assert 'name="draft"' in body             # hidden payload for Apply
    assert "Apply draft" in body              # the human gate
    assert "<svg" in body                     # SVG editor preview
    # nothing was written: the config on disk is untouched
    assert json.loads(CONFIG_PATH.read_text()) == flowcfg.load_cfg()


def test_stub_canned_draft_is_a_valid_flow():
    cfg = flowcfg.load_cfg()
    draft = json.loads(drafting.canned_draft("describe", cfg))
    assert Flow.model_validate(draft)          # shape is accepted
    assert validate(Flow.model_validate(draft)).errors == []  # and valid
    agents = [n for n in draft["nodes"] if n["type"] == "agent"]
    assert len(agents) == 2                    # mandated two-agent draft


def test_empty_description_is_refused(config_bytes):
    resp = client.post("/flow/draft", data={"description": ""})
    assert resp.status_code == 200
    assert "draft rejected" in resp.text
    assert "Describe the employee" in resp.text


# --- invalid LLM output is rejected, retried once, flow untouched ----------


def test_non_json_output_rejected_after_one_retry(config_bytes, monkeypatch):
    calls: list[int] = []

    def broken(description, cfg, system, user):
        calls.append(1)
        return "I cannot help with that."

    monkeypatch.setattr(drafting, "_generate", broken)
    resp = client.post("/flow/draft",
                       data={"description": "anything"})
    assert resp.status_code == 200
    assert "draft rejected" in resp.text
    assert "did not return a flow config" in resp.text
    assert len(calls) == 2                     # first try + exactly one retry
    assert json.loads(CONFIG_PATH.read_text()) == flowcfg.load_cfg()


def test_invalid_flow_output_rejected_with_errors(config_bytes,
                                                  monkeypatch):
    payload = json.dumps(_minimal_flow())      # connects to a missing node

    def invalid(description, cfg, system, user):
        return payload

    monkeypatch.setattr(drafting, "_generate", invalid)
    resp = client.post("/flow/draft",
                       data={"description": "anything"})
    assert resp.status_code == 200
    assert "draft rejected" in resp.text
    assert "unknown node" in resp.text         # the validator's plain error
    assert json.loads(CONFIG_PATH.read_text()) == flowcfg.load_cfg()


def test_retry_prompt_carries_the_error_list():
    system, user = drafting.build_prompts("x", {}, ["Node 'a' is broken."])
    assert "Node 'a' is broken." in user
    assert "previous attempt" in user
    # first attempt has no error section
    _, first = drafting.build_prompts("x", {}, None)
    assert "previous attempt" not in first


# --- Apply: version rows labelled "AI draft" --------------------------------


def test_apply_writes_ai_draft_version_rows(config_bytes,
                                            no_ai_draft_versions):
    cfg = flowcfg.load_cfg()
    before = copy.deepcopy(cfg)
    target = next(n for n in cfg["nodes"]
                  if n.get("node_id") == "ap_agent")
    old_node = copy.deepcopy(target)
    target["instructions"] = target.get("instructions", "") + "\nNew line."

    resp = client.post("/flow/draft/apply",
                       data={"draft": json.dumps(cfg)},
                       follow_redirects=False)
    assert resp.status_code == 303
    saved = flowcfg.load_cfg()
    saved_agent = next(n for n in saved["nodes"]
                       if n.get("node_id") == "ap_agent")
    assert saved_agent["instructions"].endswith("\nNew line.")
    # exactly the changed agent gained a version row, labelled "AI draft",
    # storing the configuration it replaced
    versions = db.list_versions("ap_agent")
    hit = [v for v in versions if v["label"] == "AI draft"]
    assert len(hit) == 1
    assert hit[0]["config"] == old_node
    unchanged = next(n for n in before["nodes"]
                     if n.get("node_id") == "classifier")
    assert not [v for v in db.list_versions("classifier")
                if v["label"] == "AI draft"]
    assert unchanged is not None


def test_apply_refuses_an_invalid_payload(config_bytes):
    cfg = flowcfg.load_cfg()
    resp = client.post("/flow/draft/apply",
                       data={"draft": json.dumps(_minimal_flow())},
                       follow_redirects=False)
    assert resp.status_code == 400
    assert "unknown node" in resp.text
    assert flowcfg.load_cfg() == cfg           # untouched
    resp = client.post("/flow/draft/apply", data={"draft": "not json"},
                       follow_redirects=False)
    assert resp.status_code == 400
    assert flowcfg.load_cfg() == cfg


# --- the draft cannot enable a tool the role does not hold ------------------


def test_role_gate_blocks_tool_the_role_cannot_call():
    cfg = flowcfg.load_cfg()
    draft = copy.deepcopy(cfg)
    agent = next(n for n in draft["nodes"] if n.get("node_id") == "ap_agent")
    agent["tools_enabled"] = ["submit_form"]   # irreversible_write tool
    errors = drafting.check(draft, role="auditor")
    assert any("submit_form" in e and "auditor" in e for e in errors)
    # control: a role with write access passes the gate
    assert not drafting.role_gate(draft, "finance_operator")


def test_draft_endpoint_uses_the_configured_role(config_bytes, monkeypatch):
    cfg = copy.deepcopy(flowcfg.load_cfg())
    cfg["session_context"]["user_role"] = "auditor"
    monkeypatch.setattr(flowcfg, "load_cfg", lambda: cfg)
    draft = copy.deepcopy(cfg)
    agent = next(n for n in draft["nodes"] if n.get("node_id") == "ap_agent")
    agent["tools_enabled"] = ["submit_form"]
    monkeypatch.setattr(
        drafting, "_generate",
        lambda d, c, s, u: json.dumps(draft))
    resp = client.post("/flow/draft",
                       data={"description": "enable form submission"})
    assert resp.status_code == 200
    assert "draft rejected" in resp.text
    assert "submit_form" in resp.text
