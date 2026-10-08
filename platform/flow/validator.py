"""Flow validation: design-time errors as plain-English messages for the dashboard.

Never raises on a bad flow - returns a list of strings (empty list = valid) so
the flow editor can render them inline. Checks: connections resolve, one start
and at least one end, agents and messages are connected, template variables
are defined by a prior agent's save_as, and enabled tools exist in the registry.
"""

from __future__ import annotations

from collections import deque

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.registry import names
from platform.flow.models import (
    AgentNode,
    ConnectorDef,
    EndNode,
    Flow,
    MessageNode,
    VerifyNode,
)

SUPPORTED_CHECK_TYPES = ("payables_state",)
SUPPORTED_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

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
    if flow.max_visits_per_node < 1:
        errors.append("max_visits_per_node must be at least 1.")
    if flow.max_total_visits < 1:
        errors.append("max_total_visits must be at least 1.")

    for node in flow.nodes:
        for target in Flow.next_ids(node):
            if target not in node_map:
                errors.append(
                    f"Node '{node.node_id}' connects to unknown node '{target}'."
                )

    registered = set(names())
    connector_names = {conn.name for conn in flow.connectors}
    for node in flow.nodes:
        if isinstance(node, AgentNode):
            if not node.next_node_ids and not node.fallback_next:
                errors.append(
                    f"Agent node '{node.node_id}' has no outgoing connection."
                )
            if node.routes and not node.save_as:
                errors.append(
                    f"Agent node '{node.node_id}' has routes but no save_as; "
                    "routing keys need a saved variable to read."
                )
            for tool_name in node.tools_enabled:
                if tool_name not in registered and tool_name not in connector_names:
                    errors.append(
                        f"Agent node '{node.node_id}' enables tool '{tool_name}', "
                        "which does not exist in the tool registry."
                    )
            if node.data_model:
                from ai_operator.datamodel import get_model

                if get_model(node.data_model) is None:
                    errors.append(
                        f"Agent node '{node.node_id}' references an unknown "
                        f"data model '{node.data_model}'."
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
    errors.extend(_connector_errors(flow))
    return errors


def _connector_errors(flow: Flow) -> list[str]:
    """Phase 3: connectors must be unique, usable over HTTP, and not collide
    with built-in registry tools (compilation registers them as tools)."""
    errors: list[str] = []
    seen: set[str] = set()
    # Registry names minus previously-synced connectors are genuinely
    # built-in; a connector whose name lands there is a collision.
    from ai_operator.tools import connectors as connector_tools

    registered = set(names())
    builtin = registered - connector_tools.registered_names()
    for conn in flow.connectors:
        if conn.name in seen:
            errors.append(f"Duplicate connector name '{conn.name}'.")
        seen.add(conn.name)
        if conn.name in builtin:
            errors.append(
                f"Connector '{conn.name}' collides with a built-in tool; "
                "pick another name."
            )
        method = str(conn.method).upper()
        if method not in SUPPORTED_HTTP_METHODS:
            errors.append(
                f"Connector '{conn.name}' has unsupported HTTP method "
                f"'{conn.method}' (supported: {', '.join(SUPPORTED_HTTP_METHODS)})."
            )
        if conn.level not in PermissionLevel._value2member_map_:
            errors.append(
                f"Connector '{conn.name}' has invalid permission level "
                f"'{conn.level}' (read, reversible_write, irreversible_write)."
            )
        if "{{" in conn.url and "}}" not in conn.url:
            errors.append(f"Connector '{conn.name}' has an unterminated URL template.")
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
