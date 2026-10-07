"""Graph assembly: a thin wrapper over the shipped flow config.

Nothing about the graph is hardcoded here anymore. configs/finance_employee.json
is validated and compiled by platform.flow.compiler at build time; the flow's
frozen session context rides along via flow_session().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_operator.session import SessionContext
from platform.flow.compiler import compile_flow
from platform.flow.models import Flow, load_flow

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FLOW_PATH = REPO_ROOT / "configs" / "finance_employee.json"

Checkpointer = Any


def load_default_flow() -> Flow:
    return load_flow(DEFAULT_FLOW_PATH)


def flow_session(flow: Flow | None = None) -> SessionContext:
    """The frozen session context the shipped flow config defines."""
    return (flow or load_default_flow()).session_context.to_session_context()


def build_graph(
    client: Any,
    checkpointer: Checkpointer = None,
) -> Any:
    """Compile the shipped flow. `client` is the model client (stub in tests)."""
    graph = compile_flow(load_default_flow(), client)
    return graph.compile(checkpointer=checkpointer)
