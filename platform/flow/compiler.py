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
from ai_operator.tools import browser as browser_tools
from ai_operator.tools import connectors as connector_tools
from ai_operator.tracing import trace_event
from ai_operator.variables import write_variable
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
    # Phase 3: config-defined external API tools are (re)registered so the
    # registry always reflects exactly the connectors of this flow.
    connector_errors = connector_tools.sync(
        connector_tools.from_defs(flow.connectors))
    if connector_errors:
        raise ValueError("cannot register connectors:\n- "
                         + "\n- ".join(connector_errors))
    first_end = next(n.node_id for n in flow.nodes if isinstance(n, EndNode))
    graph = StateGraph(AgentState)
    for node in flow.nodes:
        if isinstance(node, AgentNode):
            _add_agent(graph, node, client, first_end, flow)
        elif isinstance(node, MessageNode):
            _add_message(graph, node, first_end, flow)
        elif isinstance(node, VerifyNode):
            _add_verify(graph, node, first_end, flow)
        elif isinstance(node, EndNode):
            # End nodes are never visit-limited: the final report must always
            # be constructible, and loops burn the per-node limit first.
            graph.add_node(node.node_id, finish.run_finish)
            graph.add_edge(node.node_id, END)
    graph.add_edge(START, flow.start_node_id)
    return graph


def _with_visit_limit(flow: Flow, node_id: str, fn: Any) -> Any:
    """Wrap a flow node: count entries, stop the run when a limit is hit."""

    def guarded(state: AgentState) -> dict:
        run_id = state["run_id"]
        visits = dict(state.get("node_visits") or {})
        count = visits.get(node_id, 0)
        total = sum(visits.values())
        if count >= flow.max_visits_per_node or total >= flow.max_total_visits:
            limit = (
                flow.max_visits_per_node if count >= flow.max_visits_per_node
                else flow.max_total_visits
            )
            recent = list(state.get("history") or [])[-5:]
            last_steps = "; ".join(
                str(h.get("detail", ""))[:120] for h in recent
            ) or "(no steps yet)"
            error = (
                f"Visit limit hit at node '{node_id}' (limit {limit}). "
                f"Last steps: {last_steps}"
            )
            trace_event(run_id, "visit_limit_hit", node=node_id,
                        visits=count, total=total, limit=limit)
            return {
                "status": "needs_human",
                "node_visits": visits,
                "errors": [error],
                "last_observation": error,
                "history": [{
                    "step": int(state.get("step_count", 0)),
                    "kind": "error",
                    "detail": error,
                    "ok": False,
                }],
            }
        updates = fn(state)
        visits[node_id] = count + 1
        updates["node_visits"] = visits
        return updates

    return guarded


def _add_agent(
    graph: StateGraph, node: AgentNode, client: Any, first_end: str, flow: Flow
) -> None:
    nid = node.node_id
    persona = _resolve_system_prompt(node.system_prompt)
    documents = _load_documents(node.documents)
    allowed_tools = tuple(node.tools_enabled)
    instructions = node.instructions or None

    def entry(state: AgentState) -> dict:
        trace_event(state["run_id"], "node_entered", node=nid, node_type="agent")
        _pin(state, nid)
        updates = understand.run_understand(state, client)
        trace_event(state["run_id"], "node_exited", node=nid)
        return updates

    def decide_node(state: AgentState) -> dict:
        browser_tools.pin_agent(nid)  # idempotent; re-pins after a resume
        updates = decide.run_decide(
            state, client,
            agent_system=persona,
            agent_instructions=instructions,
            documents=documents,
        )
        if node.save_as and state.get("status") == "running":
            decision = updates.get("last_decision") or {}
            if decision.get("action_type") in ("verify", "finish"):
                answer = decision.get("expected_outcome") or decision.get("thought") or ""
                updates.update(write_variable(state, nid, node.save_as, answer))
        return updates

    def execute_node(state: AgentState, config: RunnableConfig) -> dict:
        browser_tools.pin_agent(nid)
        return execute.run_execute(state, config, allowed_tools=allowed_tools)

    def ask_node(state: AgentState) -> dict:
        browser_tools.pin_agent(nid)
        return ask.run_ask(state)

    def route_after_entry(state: AgentState) -> str:
        if state.get("status") != "running":
            _release(state, nid)
            return "fallback"
        return "decide"

    graph.add_node(nid, _with_visit_limit(flow, nid, entry))
    graph.add_node(f"{nid}/decide", decide_node)
    graph.add_node(f"{nid}/execute", execute_node)
    graph.add_node(f"{nid}/ask", ask_node)
    graph.add_conditional_edges(
        nid,
        route_after_entry,
        {"decide": f"{nid}/decide", "fallback": node.fallback_next or first_end},
    )
    edges: dict[str, str] = {
        "execute": f"{nid}/execute",
        "ask": f"{nid}/ask",
        "next": Flow.primary_next(node) or first_end,
        "fallback": node.fallback_next or first_end,
    }
    for value, target in node.routes.items():
        edges[f"route:{value}"] = target
    graph.add_conditional_edges(
        f"{nid}/decide",
        _agent_router(node),
        edges,
    )
    graph.add_edge(f"{nid}/execute", f"{nid}/decide")
    graph.add_edge(f"{nid}/ask", f"{nid}/decide")


def _pin(state: AgentState, nid: str) -> None:
    """Phase 3: this agent node owns a browser worker while it executes."""
    idx = browser_tools.pin_agent(nid)
    if idx is not None:
        trace_event(state["run_id"], "browser_pinned", node=nid, worker=idx)


def _release(state: AgentState, nid: str) -> None:
    browser_tools.release_agent(nid)
    trace_event(state["run_id"], "browser_released", node=nid)


def _agent_router(node: AgentNode):
    """Route one decide step of an agent: stay in the loop or leave the node.

    Leaving prefers node.routes[save_as value] (Phase-3 variable routing, so
    a classifier picks its specialist) and falls back to the primary next.
    Every leave releases the agent's browser worker; execute/ask keep it.
    """

    def route(state: AgentState) -> str:
        if state.get("status") != "running":
            _release(state, node.node_id)
            return "fallback"
        decision = state.get("last_decision") or {}
        action = decision.get("action_type")
        if action == "tool_call":
            return "execute"
        if action == "ask_human":
            return "ask"
        target = "next"
        if node.routes and node.save_as:
            value = str(
                (state.get("variables") or {}).get(node.save_as, "")
            ).strip()
            if value in node.routes:
                target = f"route:{value}"
            else:
                hit = next(
                    (k for k in node.routes if k.lower() == value.lower()), None
                )
                if hit is not None:
                    target = f"route:{hit}"
        _release(state, node.node_id)
        return target

    return route


def _add_message(
    graph: StateGraph, node: MessageNode, first_end: str, flow: Flow
) -> None:
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
                f"{', '.join(repr(v) for v in missing)} are not set yet."
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
        # Missing-var fallback wins even when the run is already ending, so
        # the error message's fallback still gets to render.
        if has_fallback and any(v not in variables for v in referenced):
            return "fallback"
        if state.get("status") != "running":
            return "end"
        return "next"

    graph.add_node(node.node_id, _with_visit_limit(flow, node.node_id, render))
    primary = Flow.primary_next(node) or first_end
    graph.add_conditional_edges(
        node.node_id,
        route,
        {
            "next": primary,
            "fallback": node.fallback_next or primary,
            "end": first_end,
        },
    )


def _add_verify(
    graph: StateGraph, node: VerifyNode, first_end: str, flow: Flow
) -> None:
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

    graph.add_node(node.node_id, _with_visit_limit(flow, node.node_id, run))
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
    """Load attached documents as prompt sections; missing files say so.

    Phase 3: a .pdf attachment reads its extracted .txt sibling when one
    exists (written at upload time); raw PDF bytes are never sent to the model.
    """
    loaded: list[str] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            path = REPO_ROOT / raw
        if path.suffix.lower() == ".pdf":
            txt = path.with_suffix(".txt")
            if not txt.is_file():
                txt = (REPO_ROOT / raw).with_suffix(".txt")
            if txt.is_file():
                try:
                    body = txt.read_text(encoding="utf-8")[:DOCUMENT_CHAR_LIMIT]
                except (OSError, ValueError):
                    body = "(unreadable extracted text: not utf-8)"
                if not body.strip():
                    body = "(pdf upload: text extraction failed or empty)"
                loaded.append(f"{raw} (extracted text):\n{body}")
                continue
            loaded.append(f"{raw}:\n(pdf document, no extracted text found)")
            continue
        try:
            body = path.read_text(encoding="utf-8")[:DOCUMENT_CHAR_LIMIT] \
                if path.is_file() else "(missing document)"
        except (OSError, ValueError):
            body = "(unreadable document: not utf-8 text)"
        loaded.append(f"{raw}:\n{body}")
    return loaded
