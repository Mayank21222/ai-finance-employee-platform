"""Phase 3: Classifier routing between specialists through flow config only."""

from __future__ import annotations

import json

from ai_operator.graph import load_default_flow
from ai_operator.llm import StubClient
from ai_operator.state import INITIAL_STATE_KEYS
from platform.flow.compiler import compile_flow
from platform.flow.models import Flow
from platform.flow.validator import validate

CTX = {
    "tenant": "acme",
    "currency": "INR",
    "approval_threshold": 50000.0,
    "user_role": "op",
}


def run(flow: Flow, task: str, client=None) -> dict:
    graph = compile_flow(flow, client or StubClient()).compile()
    return graph.invoke({
        "task": task,
        "run_id": "test_classifier_routing",
        **INITIAL_STATE_KEYS,
    })


def message_details(state: dict) -> str:
    return " ".join(
        h["detail"] for h in state["history"] if h.get("kind") == "message"
    )


def test_classifier_routes_reminder_request_to_reminder_agent():
    """The mandated test: a reminder request reaches reminder_agent, not ap_agent."""
    state = run(load_default_flow(), "remind all vendors with overdue invoices")
    visits = state.get("node_visits") or {}
    assert visits.get("classifier") == 1
    assert visits.get("reminder_agent", 0) >= 1, "reminder specialist never ran"
    assert "ap_agent" not in visits, "invoice specialist must not run"
    assert state["variables"]["task_type"] == "payment_reminder"
    details = message_details(state)
    assert "Payment reminder list" in details
    assert "(overdue)" in details or "no overdue invoices" in details


def test_classifier_routes_invoice_request_to_invoice_specialist():
    """invoice_entry value must leave the classifier toward ap_agent."""

    class FinishFastClient:
        """Classify, then finish immediately inside ap_agent (no browser)."""

        def complete(self, system: str, user: str) -> str:
            if '"understanding":' in system:
                return json.dumps({"understanding": "u", "plan": "p"})
            instructions = ""
            import re as _re
            m = _re.search(r"\[AGENT INSTRUCTIONS\]\n(.*?)(?=\n\[|\Z)", user,
                           flags=_re.S)
            if m:
                instructions = m.group(1)
            if "Available specialists" in instructions:
                return json.dumps({
                    "thought": "invoice wording",
                    "action_type": "finish", "tool_name": None,
                    "tool_args": {}, "plan_update": None,
                    "expected_outcome": "invoice_entry",
                })
            return json.dumps({
                "thought": "finishing without tools",
                "action_type": "finish", "tool_name": None, "tool_args": {},
                "plan_update": None, "expected_outcome": "AP DONE",
            })

    state = run(load_default_flow(),
                "enter the new vendor invoice into payables",
                client=FinishFastClient())
    visits = state.get("node_visits") or {}
    assert state["variables"]["task_type"] == "invoice_entry"
    assert visits.get("classifier") == 1
    # ap_agent was entered: the invoice_entry route reached the specialist.
    # (It loops to verification against the dead mock app until the visit
    # limit ends the run as needs_human - offline and deterministic.)
    assert visits.get("ap_agent", 0) >= 1
    assert "reminder_agent" not in visits


def test_routes_pick_target_by_saved_value():
    """Mechanism test: value saved by save_as selects the route target."""
    class GoClient:
        def complete(self, system: str, user: str) -> str:
            if '"understanding":' in system:
                return json.dumps({"understanding": "u", "plan": "p"})
            return json.dumps({
                "thought": "picking branch b",
                "action_type": "finish", "tool_name": None, "tool_args": {},
                "plan_update": None, "expected_outcome": "b",
            })

    flow = Flow.model_validate({
        "start_node_id": "router",
        "session_context": CTX,
        "nodes": [
            {"type": "agent", "node_id": "router", "save_as": "choice",
             "next_node_ids": ["a"], "fallback_next": "a",
             "routes": {"a": "a", "b": "b"}},
            {"type": "message", "node_id": "a", "template": "BRANCH A",
             "next_node_ids": ["e"]},
            {"type": "message", "node_id": "b", "template": "BRANCH B",
             "next_node_ids": ["e"]},
            {"type": "end", "node_id": "e"},
        ],
    })
    assert validate(flow) == []
    state = run(flow, "x", client=GoClient())
    assert "BRANCH B" in message_details(state)
    assert "BRANCH A" not in message_details(state)
    assert state["variables"]["choice"] == "b"


def test_route_target_is_a_known_connection():
    flow = Flow.model_validate({
        "start_node_id": "router",
        "session_context": CTX,
        "nodes": [
            {"type": "agent", "node_id": "router", "save_as": "choice",
             "next_node_ids": ["e"], "routes": {"a": "ghost"}},
            {"type": "end", "node_id": "e"},
        ],
    })
    errors = validate(flow)
    assert any("unknown node 'ghost'" in e for e in errors)


def test_routes_require_save_as():
    flow = Flow.model_validate({
        "start_node_id": "router",
        "session_context": CTX,
        "nodes": [
            {"type": "agent", "node_id": "router",
             "next_node_ids": ["e"], "routes": {"a": "e"}},
            {"type": "end", "node_id": "e"},
        ],
    })
    errors = validate(flow)
    assert any("routes but no save_as" in e for e in errors)


def test_default_flow_still_valid_with_classifier_nodes():
    flow = load_default_flow()
    assert validate(flow) == []
    ids = {n.node_id for n in flow.nodes}
    assert {"classifier", "reminder_agent", "reminder_message"} <= ids
    assert flow.start_node_id == "classifier"
