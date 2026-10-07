"""Platform flow system: validator, compiler, template rendering, visit limits."""

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


class FinishImmediatelyClient:
    """Fake model: understand succeeds, then the agent answers 'finish' at once."""

    def complete(self, system: str, user: str) -> str:
        if '"understanding":' in system:
            return json.dumps({"understanding": "u", "plan": "p"})
        return json.dumps({
            "thought": "answering immediately",
            "action_type": "finish",
            "tool_name": None,
            "tool_args": {},
            "plan_update": None,
            "expected_outcome": "THE ANSWER",
        })


class UnderstandFailsClient:
    """Fake model: understand can never parse, so the agent run fails fast."""

    def complete(self, system: str, user: str) -> str:
        return "not json at all"


def make_flow(nodes: list[dict], start: str, **overrides) -> Flow:
    return Flow.model_validate(
        {"start_node_id": start, "session_context": CTX, "nodes": nodes, **overrides}
    )


def run_flow(flow: Flow, client=None) -> dict:
    graph = compile_flow(flow, client or StubClient()).compile()
    payload = {
        "task": "platform flow unit task",
        "run_id": "test_platform_flow",
        **INITIAL_STATE_KEYS,
    }
    return graph.invoke(payload)


def message_details(state: dict) -> str:
    return " ".join(
        h["detail"] for h in state["history"] if h.get("kind") == "message"
    )


# --- validator -------------------------------------------------------------


def test_default_flow_is_valid():
    assert validate(load_default_flow()) == []


def test_validator_reports_unknown_connection_and_missing_end():
    flow = make_flow(
        [{"type": "agent", "node_id": "a", "next_node_ids": ["ghost"]}], "a"
    )
    errors = validate(flow)
    assert any("unknown node 'ghost'" in e for e in errors)
    assert any("at least one end node" in e for e in errors)


def test_validator_reports_unknown_tool_and_lonely_agent():
    flow = make_flow(
        [{"type": "agent", "node_id": "a", "tools_enabled": ["nope"]},
         {"type": "end", "node_id": "e"}],
        "a",
    )
    errors = validate(flow)
    assert any("tool 'nope'" in e for e in errors)
    assert any("no outgoing connection" in e for e in errors)


def test_validator_requires_prior_save_as_for_template_variable():
    nodes = [
        {"type": "agent", "node_id": "a", "next_node_ids": ["m"]},
        {"type": "message", "node_id": "m",
         "template": "Hello {{vars.who}}", "next_node_ids": ["e"]},
        {"type": "end", "node_id": "e"},
    ]
    errors = validate(make_flow(nodes, "a"))
    assert any("'who'" in e and "no earlier agent" in e for e in errors)

    nodes[0]["save_as"] = "who"
    assert validate(make_flow(nodes, "a")) == []


def test_validator_rejects_bad_limits_and_check_type():
    flow = make_flow(
        [{"type": "verify", "node_id": "v", "check_type": "bogus",
          "match_next": "e", "mismatch_next": "e"},
         {"type": "end", "node_id": "e"}],
        "v",
        max_visits_per_node=0,
    )
    errors = validate(flow)
    assert any("max_visits_per_node" in e for e in errors)
    assert any("unknown check type" in e for e in errors)


# --- compiler --------------------------------------------------------------


def test_compiler_produces_runnable_graph_from_default_config():
    compiled = compile_flow(load_default_flow(), StubClient()).compile()
    names = set(compiled.get_graph().nodes)
    assert {"ap_agent", "ap_agent/decide", "ap_agent/execute", "ap_agent/ask",
            "check_invoice", "report"} <= names


def test_compile_rejects_invalid_flow():
    flow = make_flow([{"type": "agent", "node_id": "a"}], "a")
    try:
        compile_flow(flow, StubClient())
    except ValueError as exc:
        assert "invalid flow" in str(exc)
    else:
        raise AssertionError("expected ValueError for invalid flow")


# --- template rendering ----------------------------------------------------


def test_message_template_renders_saved_agent_answer():
    nodes = [
        {"type": "agent", "node_id": "a", "save_as": "who",
         "tools_enabled": [], "next_node_ids": ["m"]},
        {"type": "message", "node_id": "m",
         "template": "Hello {{vars.who}}!", "next_node_ids": ["e"]},
        {"type": "end", "node_id": "e"},
    ]
    state = run_flow(make_flow(nodes, "a"), client=FinishImmediatelyClient())
    assert "Hello THE ANSWER!" in message_details(state)
    assert state["last_observation"] == "Hello THE ANSWER!"


def test_message_missing_variable_renders_error_and_routes_to_fallback():
    nodes = [
        {"type": "agent", "node_id": "a", "save_as": "x",
         "tools_enabled": [], "next_node_ids": ["e"], "fallback_next": "m"},
        {"type": "message", "node_id": "m",
         "template": "Value: {{vars.x}}",
         "next_node_ids": ["wrong"], "fallback_next": "fb"},
        {"type": "message", "node_id": "wrong",
         "template": "SHOULD NOT RUN", "next_node_ids": ["e"]},
        {"type": "message", "node_id": "fb",
         "template": "fallback used", "next_node_ids": ["e"]},
        {"type": "end", "node_id": "e"},
    ]
    state = run_flow(make_flow(nodes, "a"), client=UnderstandFailsClient())
    details = message_details(state)
    assert "TEMPLATE ERROR" in details and "'x'" in details
    assert "fallback used" in details
    assert "SHOULD NOT RUN" not in details
    assert state["last_observation"] == "fallback used"


# --- visit limits ----------------------------------------------------------


def test_per_node_visit_limit_ends_run_needs_human():
    nodes = [
        {"type": "message", "node_id": "loop",
         "template": "tick", "next_node_ids": ["loop"]},
        {"type": "end", "node_id": "e"},
    ]
    state = run_flow(make_flow(nodes, "loop"))
    assert state["status"] == "needs_human"
    assert state["node_visits"]["loop"] == 3
    report = state["report"]
    assert report["status"] == "needs_human"
    assert "Visit limit hit at node 'loop' (limit 3)" in report["errors"][0]
    assert "Last steps:" in report["errors"][0]


def test_total_visit_limit_ends_run_needs_human():
    nodes = [
        {"type": "message", "node_id": f"m{i}",
         "template": f"step {i}",
         "next_node_ids": [f"m{i + 1}"] if i < 4 else ["e"]}
        for i in range(5)
    ]
    nodes.append({"type": "end", "node_id": "e"})
    state = run_flow(make_flow(nodes, "m0", max_total_visits=3))
    assert state["status"] == "needs_human"
    assert "node 'm3'" in state["report"]["errors"][0]
