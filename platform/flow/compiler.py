"""Compile a validated flow into a LangGraph StateGraph.

Each flow node becomes one graph node. An agent node expands into four: the
entry (understand + node trace) plus decide/execute/ask satellites that form
its internal loop; only the entry counts as a visit of the flow node. Agent
config (persona, instructions, documents, tool allowlist) reaches the node
implementations through closures - never through LangGraph state - so the
model cannot edit its own rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ai_operator.nodes import ask, decide, execute, finish, understand, verify_node
from ai_operator.state import AgentState
from ai_operator.tracing import trace_event
from platform.flow.models import (
    TEMPLATE_VAR_RE,
    AgentNode,
    EndNode,
    Flow,
    MessageNode,
    VerifyNode,
)
from platform.flow.validator import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT_CHAR_LIMIT = 4000


def compile_flow(flow: Flow, client: Any) -> StateGraph:
    """Validate the flow, then build the StateGraph (not yet compiled)."""
    errors = validate(flow)
    if errors:
        raise ValueError("cannot compile an invalid flow:\n- " + "\n- ".join(errors))
    first_end = next(n.node_id for n in flow.nodes if isinstance(n, EndNode))
    graph = StateGraph(AgentState)
    for node in flow.nodes:
        if isinstance(node, AgentNode):
            _add_agent(graph, node, client, first_end)
        elif isinstance(node, MessageNode):
            _add_message(graph, node, first_end)
        elif isinstance(node, VerifyNode):
            _add_verify(graph, node, first_end)
        elif isinstance(node, EndNode):
            graph.add_node(node.node_id, finish.run_finish)
            graph.add_edge(node.node_id, END)
    graph.add_edge(START, flow.start_node_id)
    return graph


def _add_agent(graph: StateGraph, node: AgentNode, client: Any, first_end: str) -> None:
    nid = node.node_id
    persona = _resolve_system_prompt(node.system_prompt)
    documents = _load_documents(node.documents)
    allowed_tools = tuple(node.tools_enabled)
    instructions = node.instructions or None

    def entry(state: AgentState) -> dict:
        trace_event(state["run_id"], "node_entered", node=nid, node_type="agent")
        updates = understand.run_understand(state, client)
        trace_event(state["run_id"], "node_exited", node=nid)
        return updates

    def decide_node(state: AgentState) -> dict:
        return decide.run_decide(
            state, client,
            agent_system=persona,
            agent_instructions=instructions,
            documents=documents,
        )

    def execute_node(state: AgentState, config: RunnableConfig) -> dict:
        return execute.run_execute(state, config, allowed_tools=allowed_tools)

    graph.add_node(nid, entry)
    graph.add_node(f"{nid}/decide", decide_node)
    graph.add_node(f"{nid}/execute", execute_node)
    graph.add_node(f"{nid}/ask", ask.run_ask)
    graph.add_edge(nid, f"{nid}/decide")
    graph.add_conditional_edges(
        f"{nid}/decide",
        _agent_router(),
        {
            "execute": f"{nid}/execute",
            "ask": f"{nid}/ask",
            "next": Flow.primary_next(node) or first_end,
            "fallback": node.fallback_next or first_end,
        },
    )
    graph.add_edge(f"{nid}/execute", f"{nid}/decide")
    graph.add_edge(f"{nid}/ask", f"{nid}/decide")


def _agent_router():
    """Route one decide step of an agent: stay in the loop or leave the node."""

    def route(state: AgentState) -> str:
        if state.get("status") != "running":
            return "fallback"
        decision = state.get("last_decision") or {}
        action = decision.get("action_type")
        if action == "tool_call":
            return "execute"
        if action == "ask_human":
            return "ask"
        return "next"

    return route


def _add_message(graph: StateGraph, node: MessageNode, first_end: str) -> None:
    referenced = Flow.template_variables(node)
    has_fallback = bool(node.fallback_next)

    def render(state: AgentState) -> dict:
        run_id = state["run_id"]
        trace_event(run_id, "node_entered", node=node.node_id, node_type="message")
        variables = state.get("variables") or {}
        missing = [v for v in referenced if v not in variables]
        if missing:
            rendered = (
                f"TEMPLATE ERROR in message node '{node.node_id}': variable(s) "
                f"{', '.join(missing)} are not set yet."
            )
            trace_event(run_id, "message_rendered", node=node.node_id,
                        missing=missing, error=True)
        else:
            rendered = TEMPLATE_VAR_RE.sub(
                lambda m: str(variables.get(m.group(1), "")), node.template
            )
            trace_event(run_id, "message_rendered", node=node.node_id,
                        rendered=rendered)
        trace_event(run_id, "node_exited", node=node.node_id)
        return {
            "last_observation": rendered,
            "history": [{
                "step": int(state.get("step_count", 0)),
                "kind": "message",
                "detail": rendered,
                "ok": not missing,
            }],
        }

    def route(state: AgentState) -> str:
        variables = state.get("variables") or {}
        if has_fallback and any(v not in variables for v in referenced):
            return "fallback"
        return "next"

    graph.add_node(node.node_id, render)
    primary = Flow.primary_next(node) or first_end
    graph.add_conditional_edges(
        node.node_id,
        route,
        {"next": primary, "fallback": node.fallback_next or primary},
    )


def _add_verify(graph: StateGraph, node: VerifyNode, first_end: str) -> None:
    def run(state: AgentState) -> dict:
        trace_event(state["run_id"], "node_entered", node=node.node_id,
                    node_type="verify")
        updates = verify_node.run_verify(state)
        trace_event(state["run_id"], "node_exited", node=node.node_id)
        return updates

    def route(state: AgentState) -> str:
        verification = state.get("verification") or {}
        if verification.get("matched"):
            return "match"
        if state.get("status") != "running":
            return "end"
        return "mismatch"

    graph.add_node(node.node_id, run)
    graph.add_conditional_edges(
        node.node_id,
        route,
        {
            "match": node.match_next,
            "mismatch": node.mismatch_next,
            "end": first_end,
        },
    )


def _resolve_system_prompt(text: str) -> str | None:
    """Agent persona: a prompt file path if one exists, else inline text."""
    if not text:
        return None
    candidate = Path(text)
    for path in (candidate, REPO_ROOT / candidate):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return text


def _load_documents(paths: list[str]) -> list[str]:
    """Load attached documents as prompt sections; missing files say so."""
    loaded: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            path = REPO_ROOT / raw
        try:
            body = path.read_text(encoding="utf-8")[:DOCUMENT_CHAR_LIMIT] \
                if path.is_file() else "(missing document)"
        except OSError:
            body = "(missing document)"
        loaded.append(f"{raw}:\n{body}")
    return loaded
