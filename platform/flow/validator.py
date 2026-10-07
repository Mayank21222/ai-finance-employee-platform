"""Flow validation: design-time errors as plain-English messages for the dashboard.

Never raises on a bad flow - returns a list of strings (empty list = valid) so
the flow editor can render them inline. Checks: connections resolve, one start
and at least one end, agents and messages are connected, template variables
are defined by a prior agent's save_as, and enabled tools exist in the registry.
"""

from __future__ import annotations

from collections import deque

from ai_operator.tools.registry import names
from platform.flow.models import (
    AgentNode,
    EndNode,
    Flow,
    MessageNode,
    VerifyNode,
)

SUPPORTED_CHECK_TYPES = ("payables_state",)

INITIAL_SESSION_VARIABLE_KEYS: set[str] = set()
"""Variables defined before any node runs; the variables system extends this."""


def validate(flow: Flow) -> list[str]:
    errors: list[str] = []
    node_map = flow.node_map()
    if len(node_map) != len(flow.nodes):
        seen: set[str] = set()
        for node in flow.nodes:
            if node.node_id in seen:
                errors.append(f"Duplicate node id '{node.node_id}'.")
            seen.add(node.node_id)

    if flow.start_node_id not in node_map:
        errors.append(
            f"Start node '{flow.start_node_id}' does not exist in the flow."
        )
    if not any(isinstance(n, EndNode) for n in flow.nodes):
        errors.append("The flow needs at least one end node.")

    for node in flow.nodes:
        for target in Flow.next_ids(node):
            if target not in node_map:
                errors.append(
                    f"Node '{node.node_id}' connects to unknown node '{target}'."
                )

    for node in flow.nodes:
        if isinstance(node, AgentNode):
            if not node.next_node_ids and not node.fallback_next:
                errors.append(
                    f"Agent node '{node.node_id}' has no outgoing connection."
                )
            for tool_name in node.tools_enabled:
                if tool_name not in names():
                    errors.append(
                        f"Agent node '{node.node_id}' enables tool '{tool_name}', "
                        "which does not exist in the tool registry."
                    )
        elif isinstance(node, MessageNode):
            if not node.next_node_ids and not node.fallback_next:
                errors.append(
                    f"Message node '{node.node_id}' has no outgoing connection."
                )
        elif isinstance(node, VerifyNode):
            if not node.match_next:
                errors.append(
                    f"Verify node '{node.node_id}' is missing its match connection."
                )
            if not node.mismatch_next:
                errors.append(
                    f"Verify node '{node.node_id}' is missing its mismatch connection."
                )
            if node.check_type not in SUPPORTED_CHECK_TYPES:
                errors.append(
                    f"Verify node '{node.node_id}' has unknown check type "
                    f"'{node.check_type}' (supported: {', '.join(SUPPORTED_CHECK_TYPES)})."
                )

    errors.extend(_template_variable_errors(flow, node_map))
    return errors


def _template_variable_errors(flow: Flow, node_map: dict) -> list[str]:
    """A message's {{vars.x}} must be defined by an agent visited before it."""
    errors: list[str] = []
    defined = set(INITIAL_SESSION_VARIABLE_KEYS)
    visited: set[str] = set()
    queue: deque[str] = deque([flow.start_node_id])
    while queue:
        node_id = queue.popleft()
        if node_id in visited or node_id not in node_map:
            continue
        visited.add(node_id)
        node = node_map[node_id]
        if isinstance(node, AgentNode) and node.save_as:
            defined.add(node.save_as)
        elif isinstance(node, MessageNode):
            for var in Flow.template_variables(node):
                if var not in defined:
                    errors.append(
                        f"Message node '{node.node_id}' references variable "
                        f"'{var}', which no earlier agent node saves."
                    )
        queue.extend(Flow.next_ids(node))
    return errors
