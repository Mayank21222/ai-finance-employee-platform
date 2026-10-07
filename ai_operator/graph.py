"""Graph assembly: one LLM decide step surrounded by tools, checks, and pauses."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from ai_operator.nodes import ask, decide, execute, finish, understand, verify_node
from ai_operator.state import AgentState

Checkpointer = Any


def build_graph(
    client: Any,
    checkpointer: Checkpointer = None,
) -> Any:
    """Compile the operator graph. `client` is the model client (stub in tests)."""
    graph = StateGraph(AgentState)

    graph.add_node("understand", lambda s: understand.run_understand(s, client))
    graph.add_node("decide", lambda s: decide.run_decide(s, client))
    graph.add_node("execute", execute.run_execute)
    graph.add_node("ask", ask.run_ask)
    graph.add_node("verify", verify_node.run_verify)
    graph.add_node("finish", finish.run_finish)

    graph.add_edge(START, "understand")
    graph.add_edge("understand", "decide")
    graph.add_conditional_edges(
        "decide",
        route_after_decide,
        ["execute", "ask", "verify", "finish"],
    )
    graph.add_edge("execute", "decide")
    graph.add_edge("ask", "decide")
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        ["decide", "finish"],
    )
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


def route_after_decide(state: AgentState) -> str:
    if state.get("status") != "running":
        return "finish"
    decision = state.get("last_decision") or {}
    action = decision.get("action_type")
    if action == "tool_call":
        return "execute"
    if action == "ask_human":
        return "ask"
    if action == "verify":
        return "verify"
    return "finish"


def route_after_verify(state: AgentState) -> str:
    verification = state.get("verification") or {}
    if verification.get("matched"):
        return "finish"
    if state.get("status") != "running":
        return "finish"
    return "decide"
