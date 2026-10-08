"""Phase 4 additions: usage/config mode, data models, field permissions,
connector registry page, SVG flow editor, audit trail."""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

_SHARED = tempfile.mkdtemp(prefix="phase4_dash_")
os.environ["DASH_DB"] = os.path.join(_SHARED, "dashboard.db")
os.environ["STUB_MODEL"] = "1"

from ai_operator import datamodel
from ai_operator.state import INITIAL_STATE_KEYS
from ai_operator.tools import connectors as connector_tools
from ai_operator.tools.registry import get, names
from platform.dashboard import db
from platform.dashboard import app as dashapp

client = TestClient(dashapp.app)
db.init_db()


def test_mode_toggle_switches_body_attribute_and_persists():
    # Default (no cookie): usage mode - edit surfaces carry mode-edit class.
    resp = client.get("/runs")
    assert 'data-mode="use"' in resp.text
    assert 'href="/mode/configure"' in resp.text  # toggle offers Configure
    assert "mode-edit" in resp.text
    # Configure mode: cookie set, body attribute flips, toggle now readies Use.
    resp = client.get("/mode/configure", headers={"Referer": "/runs"},
                      follow_redirects=False)
    assert resp.status_code == 303
    assert "app_mode=configure" in resp.headers.get("set-cookie", "")
    resp = client.get("/runs")
    assert 'data-mode="configure"' in resp.text
    assert 'href="/mode/use"' in resp.text  # toggle now readies Use mode
    # Run surfaces are mode-run and hidden in configure mode (CSS contract).
    assert 'class="active mode-run"' in resp.text
    resp = client.get("/mode/use", follow_redirects=False)
    assert "app_mode=use" in resp.headers.get("set-cookie", "")
    resp = client.get("/runs")
    assert 'data-mode="use"' in resp.text


def _models_reset(keep: list[str]):
    datamodel.save_models([m for m in datamodel.load_models()
                           if m.name in keep])


def test_data_models_crud_cycle():
    """Mandated Phase 4 test: the data models page CRUD cycle."""
    try:
        resp = client.get("/models")
        assert "Invoice" in resp.text
        for name in ("vendor_name", "invoice_number", "amount",
                     "due_date", "purchase_order_ref"):
            assert name in resp.text
        # Create
        resp = client.post("/models", data={"name": "ScratchVendor",
                                            "description": "scratch"},
                           follow_redirects=False)
        assert resp.status_code == 303
        assert "ScratchVendor" in client.get("/models").text
        # Add a typed, required field with permissions
        resp = client.post("/models/ScratchVendor/fields", data={
            "field_name": "vendor_id", "field_type": "text",
            "required": "1", "description": "vendor reference",
            "write_agents": "ap_agent",
            "read_agents": "ap_agent",
        }, follow_redirects=False)
        assert resp.status_code == 303
        body = client.get("/models").text
        assert "vendor_id" in body and "required" in body
        # Duplicate field rejected
        resp = client.post("/models/ScratchVendor/fields", data={
            "field_name": "vendor_id", "field_type": "text",
            "required": "", "description": "", "write_agents": "",
            "read_agents": "",
        })
        assert resp.status_code == 400
        # Invalid type rejected
        resp = client.post("/models/ScratchVendor/fields", data={
            "field_name": "oops", "field_type": "gibberish",
            "required": "", "description": "", "write_agents": "",
            "read_agents": "",
        })
        assert resp.status_code == 400
        # Delete the scratch model
        resp = client.post("/models/ScratchVendor/delete", follow_redirects=False)
        assert resp.status_code == 303
        body = client.get("/models").text
        assert "ScratchVendor" not in body and "Invoice" in body
    finally:
        _models_reset(["Invoice"])


def test_data_model_fields_drive_prompt_variables_and_verifier():
    """Mandated Phase 4 test: attach a data model, run the demo flow, and
    confirm the variables written during the run match the model's field
    names exactly; the verifier type-checks amount (number) and due_date
    (date) from the model, and the field list reached the agent's prompt."""

    FIELDS = {
        "vendor_name": "Acme Corp",
        "invoice_number": "INV-9001",
        "amount": "42500.0",
        "due_date": "2026-07-30",
        "purchase_order_ref": "PO-101",
    }
    TYPES = {"vendor_name": "text", "invoice_number": "text",
             "amount": "number", "due_date": "date",
             "purchase_order_ref": "text"}
    entry_ids = [f"entry_{n}" for n in FIELDS]

    class ExtractClient:
        def __init__(self):
            self.seen_systems: list[str] = []

        def complete(self, system: str, user: str) -> str:
            self.seen_systems.append(system)
            if '"understanding":' in system:
                return json.dumps({"understanding": "u", "plan": "p"})
            # Each agent's instructions name the field it extracts.
            import re as _re
            m = _re.search(r"\[AGENT INSTRUCTIONS\]\n(.*)", user)
            field = m.group(1).strip() if m else ""
            return json.dumps({
                "thought": f"extracting {field}",
                "action_type": "finish", "tool_name": None,
                "tool_args": {}, "plan_update": None,
                "expected_outcome": FIELDS.get(field, ""),
            })

    scratch = datamodel.DataModel(
        name="E2EInvoice",
        description="scratch mirror of Invoice granting write to entry nodes",
        fields=[datamodel.FieldSpec(
            name=f, type=TYPES[f], required=True,
            write_agents=entry_ids, read_agents=entry_ids + ["ap_agent"])
            for f in FIELDS],
    )
    datamodel.save_models(datamodel.load_models() + [scratch])
    flow_cls = __import__("platform.flow.models", fromlist=["Flow"]).Flow
    nodes = []
    for i, nid in enumerate(entry_ids):
        nxt = entry_ids[i + 1] if i + 1 < len(entry_ids) else "check"
        nodes.append({"type": "agent", "node_id": nid,
                      "data_model": "E2EInvoice", "save_as": list(FIELDS)[i],
                      "instructions": list(FIELDS)[i],
                      "next_node_ids": [nxt], "fallback_next": nxt})
    nodes.append({"type": "verify", "node_id": "check",
                  "match_next": "report", "mismatch_next": "report"})
    nodes.append({"type": "end", "node_id": "report"})
    flow = flow_cls.model_validate({
        "start_node_id": "entry_vendor_name",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator",
                            "tools_enabled": []},
        "nodes": nodes,
    })
    client_obj = ExtractClient()
    from platform.flow.compiler import compile_flow

    graph = compile_flow(flow, client_obj).compile()
    final = graph.invoke({
        "task": "extract the invoice fields",
        "run_id": "p4_field_match_" + uuid.uuid4().hex[:8],
        **INITIAL_STATE_KEYS,
    })
    try:
        variables = final["variables"]
        assert set(variables) == set(FIELDS), (
            "written variables must match the data model's field names exactly")
        assert any("== Data model: E2EInvoice ==" in s
                   for s in client_obj.seen_systems)
        mc = final["verification"]["model_check"]
        assert mc and mc["matched"]
        assert mc["fields"]["amount"]["ok"]
        assert mc["fields"]["due_date"]["ok"]
    finally:
        _models_reset(["Invoice", "E2EInvoice"])


def test_field_level_permission_rejects_unauthorized_write():
    """Mandated Phase 4 test: field-level permission rejection.

    An agent outside a field's write_agents list cannot write that field -
    the write is refused in code (not in a prompt), traced as
    permission_denied, and the variable stays unset. The same write by an
    allowed agent goes through, and a non-model session variable (task_type)
    stays unrestricted for everyone.
    """
    from ai_operator.tracing import RUNS_ROOT, TraceLogger, unregister
    from ai_operator.variables import write_permission
    from platform.flow.compiler import compile_flow
    from platform.flow.models import Flow

    class FinishClient:
        def complete(self, system: str, user: str) -> str:
            if '"understanding":' in system:
                return json.dumps({"understanding": "u", "plan": "p"})
            return json.dumps({
                "thought": "writing the amount field",
                "action_type": "finish", "tool_name": None, "tool_args": {},
                "plan_update": None, "expected_outcome": "99999",
            })

    def run_as(node_id: str) -> tuple[dict, list[dict]]:
        run_id = "p4_perm_" + uuid.uuid4().hex[:8]
        flow = Flow.model_validate({
            "start_node_id": node_id,
            "session_context": {"tenant": "acme", "currency": "INR",
                                "approval_threshold": 50000.0,
                                "user_role": "finance_operator",
                                "tools_enabled": []},
            "nodes": [
                {"type": "agent", "node_id": node_id,
                 "data_model": "Invoice", "save_as": "amount",
                 "instructions": "amount",
                 "next_node_ids": ["report"], "fallback_next": "report"},
                {"type": "end", "node_id": "report"},
            ],
        })
        logger = TraceLogger(run_id)
        try:
            final = compile_flow(flow, FinishClient()).compile().invoke({
                "task": "set the amount field",
                "run_id": run_id,
                **INITIAL_STATE_KEYS,
            })
            return final, logger.events()
        finally:
            unregister(run_id)
            shutil.rmtree(RUNS_ROOT / run_id, ignore_errors=True)

    # A read-only agent (not in amount's write_agents) is refused...
    denied, events = run_as("policy_agent")
    assert "amount" not in denied["variables"], (
        "a denied write must never touch state['variables']")
    assert str(denied["last_observation"]).startswith("PERMISSION DENIED")
    denied_events = [e for e in events if e["event"] == "permission_denied"]
    assert denied_events, "the refusal must be traced as permission_denied"
    assert denied_events[0]["node"] == "policy_agent"
    assert denied_events[0]["name"] == "amount"
    assert denied_events[0]["new"] == "99999"

    # ...while ap_agent (listed in write_agents) writes the same field.
    allowed, _ = run_as("ap_agent")
    assert allowed["variables"].get("amount") == "99999"

    # Session variables that are not model fields stay unrestricted.
    assert write_permission("classifier", "task_type") == ""