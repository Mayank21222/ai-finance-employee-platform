"""Session variables: mutable working memory stored in state['variables'].

The frozen half is ai_operator.session.SessionContext (never in state); this
module is the writable half - extracted fields, check results, agent answers
saved via save_as. Every write must go through write_variable so the trace
records which node changed what, from which value to which value.
"""

from __future__ import annotations

from typing import Any

from ai_operator.state import AgentState
from ai_operator.tracing import trace_event


def write_variable(state: AgentState, node: str, name: str, value: Any) -> dict:
    """Return a state update that sets one session variable, traced with old/new."""
    variables = dict(state.get("variables") or {})
    old = variables.get(name)
    variables[name] = value
    trace_event(
        state["run_id"], "variable_written",
        node=node, name=name, old=old, new=value,
    )
    return {"variables": variables}
