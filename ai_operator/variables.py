"""Session variables: mutable working memory stored in state['variables'].

The frozen half is ai_operator.session.SessionContext (never in state); this
module is the writable half - extracted fields, check results, agent answers
saved via save_as. Every write must go through write_variable so the trace
records which node changed what, from which value to which value.

Phase 4: a write to a data model's field is permission-checked in code. An
agent may only write a field listed in that field's write_agents; a denied
attempt never touches state['variables'], is traced as permission_denied and
becomes the observation the agent sees next.
"""

from __future__ import annotations

from typing import Any

from ai_operator.datamodel import write_permission
from ai_operator.state import AgentState
from ai_operator.tracing import trace_event


def write_variable(state: AgentState, node: str, name: str, value: Any,
                   model: str | None = None) -> dict:
    """Return a state update that sets one session variable, traced with old/new.

    Returns a permission_denied update (variable untouched) when the writing
    node is not allowed to write a data-model field with this name. `model` is
    the writing agent's declared data model (its extraction contract).
    """
    variables = dict(state.get("variables") or {})
    denied = write_permission(node, name, model)
    if denied == "deny":
        detail = (
            f"Agent '{node}' is not allowed to write '{name}' (data model "
            "field permission; read-only on this field)."
        )
        trace_event(
            state["run_id"], "permission_denied",
            node=node, name=name, new=value, detail=detail,
        )
        return {
            "last_observation": f"PERMISSION DENIED: {detail}",
            "history": [{
                "step": int(state.get("step_count", 0)),
                "kind": "error",
                "detail": detail,
                "ok": False,
            }],
        }
    old = variables.get(name)
    variables[name] = value
    trace_event(
        state["run_id"], "variable_written",
        node=node, name=name, old=old, new=value,
    )
    return {"variables": variables}