"""Flow validation: design-time errors as plain-English messages for the dashboard.

Never raises on a bad flow - returns a ValidationResult with errors (blocking)
and warnings (non-blocking guardrail lint). Checks: connections resolve, one start
and at least one end, agents and messages are connected, template variables
are defined by a prior agent's save_as, and enabled tools exist in the registry.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import List

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


@dataclass
class ValidationResult:
    """Result of flow validation with separate errors and warnings."""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        """True if there are any errors (for backward compatibility in conditionals)."""
        return bool(self.errors)

    def __iter__(self):
        """Iterate over errors for backward compatibility."""
        return iter(self.errors)

    def __eq__(self, other: object) -> bool:
        """`validate(f) == []` still means "no blocking errors" (legacy tests).

        Comparing against a list compares only the blocking errors, so guardrail
        warnings never make an otherwise-valid flow unequal to [].
        """
        if isinstance(other, list):
            return self.errors == other
        if isinstance(other, ValidationResult):
            return (self.errors, self.warnings) == (other.errors, other.warnings)
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    @property
    def guardrail_score(self) -> int:
        """Number of warnings (guardrail score)."""
        return len(self.warnings)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def extend(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def to_errors_only(self) -> List[str]:
        """Return only errors for backward compatibility."""
        return list(self.errors)


def validate(flow: Flow) -> ValidationResult:
    """Validate a flow, returning both blocking errors and non-blocking warnings."""
    result = ValidationResult()
    node_map = flow.node_map()
    if len(node_map) != len(flow.nodes):
        seen: set[str] = set()
        for node in flow.nodes:
            if node.node_id in seen:
                result.add_error(f"Duplicate node id '{node.node_id}'.")
            seen.add(node.node_id)

    if flow.start_node_id not in node_map:
        result.add_error(
            f"Start node '{flow.start_node_id}' does not exist in the flow."
        )
    if not any(isinstance(n, EndNode) for n in flow.nodes):
        result.add_error("The flow needs at least one end node.")
    if flow.max_visits_per_node < 1:
        result.add_error("max_visits_per_node must be at least 1.")
    if flow.max_total_visits < 1:
        result.add_error("max_total_visits must be at least 1.")

    for node in flow.nodes:
        for target in Flow.next_ids(node):
            if target not in node_map:
                result.add_error(
                    f"Node '{node.node_id}' connects to unknown node '{target}'."
                )

    registered = set(names())
    connector_names = {conn.name for conn in flow.connectors}
    for node in flow.nodes:
        if isinstance(node, AgentNode):
            if not node.next_node_ids and not node.fallback_next:
                result.add_error(
                    f"Agent node '{node.node_id}' has no outgoing connection."
                )
            if node.routes and not node.save_as:
                result.add_error(
                    f"Agent node '{node.node_id}' has routes but no save_as; "
                    "routing keys need a saved variable to read."
                )
            for tool_name in node.tools_enabled:
                if tool_name not in registered and tool_name not in connector_names:
                    result.add_error(
                        f"Agent node '{node.node_id}' enables tool '{tool_name}', "
                        "which does not exist in the tool registry."
                    )
            if node.data_model:
                from ai_operator.datamodel import get_model

                if get_model(node.data_model) is None:
                    result.add_error(
                        f"Agent node '{node.node_id}' references an unknown "
                        f"data model '{node.data_model}'."
                    )
        elif isinstance(node, MessageNode):
            if not node.next_node_ids and not node.fallback_next:
                result.add_error(
                    f"Message node '{node.node_id}' has no outgoing connection."
                )
        elif isinstance(node, VerifyNode):
            if not node.match_next:
                result.add_error(
                    f"Verify node '{node.node_id}' is missing its match connection."
                )
            if not node.mismatch_next:
                result.add_error(
                    f"Verify node '{node.node_id}' is missing its mismatch connection."
                )
            if node.check_type not in SUPPORTED_CHECK_TYPES:
                result.add_error(
                    f"Verify node '{node.node_id}' has unknown check type "
                    f"'{node.check_type}' (supported: {', '.join(SUPPORTED_CHECK_TYPES)})."
                )

    result.extend(_template_variable_errors(flow, node_map))
    result.extend(_connector_errors(flow))
    result.extend(_skill_errors(flow))
    result.extend(_role_errors(flow))
    result.extend(_guardrail_warnings(flow, node_map))
    return result


def _guardrail_warnings(flow: Flow, node_map: dict) -> ValidationResult:
    """Phase 6 Section 7: Guardrail lint - non-blocking warnings for common issues."""
    result = ValidationResult()

    # Build a map of which variables are definitely set before each node
    # by analyzing all paths from start to each node.
    vars_definitely_set = _compute_definitely_set_variables(flow, node_map)

    # Get role permissions for checking field write access
    role_field_write = _get_role_field_write_permissions()

    for node in flow.nodes:
        if isinstance(node, AgentNode):
            # Warning 1: Agent has write-level tool but no approval rule applies
            _check_write_tools_without_approval(node, flow, result)

            # Warning 2: Agent has browser tool but no visit limit override
            _check_browser_tool_without_visit_limit(node, flow, result)

            # Warning 4: Agent's role has write on field not mentioned in tools/instructions
            _check_role_write_on_unused_field(node, role_field_write, result)

            # Warning 5: Connector with write permission enabled for read-only role
            _check_write_connector_for_readonly_role(node, flow, result)

            # Warning 6: Agent has no data model but instructions mention extracting fields
            _check_missing_data_model_for_extraction(node, result)

        elif isinstance(node, MessageNode):
            # Warning 3: Message node template references variable only some paths set
            _check_message_template_partial_vars(node, flow, node_map, vars_definitely_set, result)

    return result


def _compute_definitely_set_variables(flow: Flow, node_map: dict) -> dict[str, set[str]]:
    """Compute variables that are definitely set on ALL paths to each node.

    Returns a dict mapping node_id -> set of variable names that are guaranteed
    to be set before that node executes, considering all possible paths.
    """
    # For each node, track the set of variables that COULD be set (any path)
    # and the set that ARE set on ALL paths (definitely set).
    # We use a fixpoint iteration over the graph.

    # Initialize: start node has initial session variables
    definitely_set: dict[str, set[str]] = {flow.start_node_id: set(INITIAL_SESSION_VARIABLE_KEYS)}
    possibly_set: dict[str, set[str]] = {flow.start_node_id: set(INITIAL_SESSION_VARIABLE_KEYS)}

    # Iterate until fixpoint
    changed = True
    while changed:
        changed = False
        for node in flow.nodes:
            node_id = node.node_id
            # Compute inputs from all predecessors
            preds = _get_predecessors(node_id, flow)
            if not preds:
                continue

            # Definitely set = intersection of definitely_set of all predecessors
            # plus any save_as from predecessor agents that definitely run
            pred_definitely = None
            pred_possibly = set()
            for pred_id in preds:
                if pred_id in definitely_set:
                    if pred_definitely is None:
                        pred_definitely = definitely_set[pred_id].copy()
                    else:
                        pred_definitely &= definitely_set[pred_id]
                    pred_possibly |= possibly_set.get(pred_id, set())
                else:
                    pred_definitely = set()
                    pred_possibly = set()

            if pred_definitely is None:
                pred_definitely = set()

            # Add save_as from predecessor agents that are on all paths
            for pred_id in preds:
                pred_node = node_map.get(pred_id)
                if isinstance(pred_node, AgentNode) and pred_node.save_as:
                    # If this predecessor is on ALL paths to current node, its save_as is definite
                    if all(p == pred_id for p in preds):  # Only one predecessor
                        pred_definitely.add(pred_node.save_as)
                    pred_possibly.add(pred_node.save_as)

            # Update if changed
            old_def = definitely_set.get(node_id, set())
            old_poss = possibly_set.get(node_id, set())
            if pred_definitely != old_def or pred_possibly != old_poss:
                definitely_set[node_id] = pred_definitely
                possibly_set[node_id] = pred_possibly
                changed = True

    return definitely_set


def _get_predecessors(node_id: str, flow: Flow) -> List[str]:
    """Get all nodes that have an edge to the given node."""
    preds = []
    for node in flow.nodes:
        if node_id in Flow.next_ids(node):
            preds.append(node.node_id)
    return preds


def _check_write_tools_without_approval(node: AgentNode, flow: Flow, result: ValidationResult) -> None:
    """Warning 1: Agent has write-level tool but no approval rule or threshold applies."""
    from ai_operator.tools.registry import get as get_tool
    from ai_operator.permissions import PermissionLevel

    write_tools = []
    for tool_name in node.tools_enabled:
        try:
            tool = get_tool(tool_name)
            if tool.level in (PermissionLevel.reversible_write, PermissionLevel.irreversible_write):
                write_tools.append(tool_name)
        except Exception:
            pass

    if not write_tools:
        return

    # Check if any approval mechanism applies
    has_approval = False
    # Check session context approval threshold
    if flow.session_context.approval_on_amount_over_threshold and flow.session_context.approval_threshold > 0:
        has_approval = True
    # Check if any tool is irreversible_write (always requires approval)
    for tool_name in write_tools:
        try:
            tool = get_tool(tool_name)
            if tool.level == PermissionLevel.irreversible_write:
                has_approval = True
                break
        except Exception:
            pass
    # Check approval_trigger_levels
    if flow.session_context.approval_trigger_levels:
        has_approval = True

    if not has_approval:
        result.add_warning(
            f"Agent '{node.node_id}' has write-level tools ({', '.join(write_tools)}) "
            "but no approval rule or threshold applies to them."
        )


def _check_browser_tool_without_visit_limit(node: AgentNode, flow: Flow, result: ValidationResult) -> None:
    """Warning 2: Agent has browser tool but no visit limit override below platform default."""
    browser_tools = {"navigate", "read_page", "click", "fill", "submit_form", "screenshot"}
    has_browser_tool = any(t in browser_tools for t in node.tools_enabled)

    if not has_browser_tool:
        return

    # Check if this agent's node has a visit limit override (via flow config)
    # The platform default is max_visits_per_node (default 3)
    if flow.max_visits_per_node >= 3:  # Platform default
        result.add_warning(
            f"Agent '{node.node_id}' has browser tools but uses the platform default "
            f"visit limit ({flow.max_visits_per_node}). Consider setting a lower "
            "per-node limit for browser-heavy agents."
        )


def _check_message_template_partial_vars(
    node: MessageNode, flow: Flow, node_map: dict,
    vars_definitely_set: dict[str, set[str]], result: ValidationResult
) -> None:
    """Warning 3: Message template references variable only some paths set."""
    template_vars = Flow.template_variables(node)
    if not template_vars:
        return

    definitely_set = vars_definitely_set.get(node.node_id, set())
    for var in template_vars:
        if var not in definitely_set:
            # Check if it's possibly set (on some paths)
            # We need to check if there's at least one path where it's set
            # For simplicity, check if any predecessor agent saves it
            preds = _get_predecessors(node.node_id, flow)
            possibly_set = False
            for pred_id in preds:
                pred_node = node_map.get(pred_id)
                if isinstance(pred_node, AgentNode) and pred_node.save_as == var:
                    possibly_set = True
                    break

            if possibly_set:
                result.add_warning(
                    f"Message node '{node.node_id}' template references variable "
                    f"'{var}' which is only set on some paths leading to this node. "
                    "Consider adding a fallback or ensuring all paths set it."
                )


def _get_role_field_write_permissions() -> dict[str, dict[str, str]]:
    """Get role -> field -> access mapping from roles database."""
    try:
        from platform.dashboard import db
        from ai_operator import roles as role_mod

        perms = role_mod.role_permission_map()
        field_perms: dict[str, dict[str, str]] = {}
        for role_name, resources in perms.items():
            for resource, (access, _) in resources.items():
                if resource.startswith("field:"):
                    field_name = resource[6:]  # Remove "field:" prefix
                    field_perms.setdefault(role_name, {})[field_name] = access
        return field_perms
    except Exception:
        return {}


def _check_role_write_on_unused_field(node: AgentNode, role_field_write: dict, result: ValidationResult) -> None:
    """Warning 4: Agent's role has write on a field that no tool or instruction mentions."""
    if not node.role or node.role not in role_field_write:
        return

    # Get fields this role can write
    writable_fields = {
        field for field, access in role_field_write[node.role].items()
        if access in ("write", "approve")
    }

    if not writable_fields:
        return

    # Check if any tool or instruction mentions these fields
    mentioned_fields = set()
    # Check tools that might write fields
    from ai_operator.tools.registry import get as get_tool
    for tool_name in node.tools_enabled:
        try:
            tool = get_tool(tool_name)
            # Check tool args model for field-like names
            for field_name in tool.args_model.model_fields:
                if field_name in writable_fields:
                    mentioned_fields.add(field_name)
        except Exception:
            pass

    # Check instructions for field names (simple keyword search)
    instructions_lower = (node.instructions or "").lower()
    for field in writable_fields:
        if field.lower() in instructions_lower:
            mentioned_fields.add(field)

    # Check data model fields if attached
    if node.data_model:
        from ai_operator.datamodel import get_model
        model = get_model(node.data_model)
        if model:
            for field in model.fields:
                if field.name in writable_fields:
                    mentioned_fields.add(field.name)

    unused_writable = writable_fields - mentioned_fields
    for field in unused_writable:
        result.add_warning(
            f"Agent '{node.node_id}' (role: {node.role}) has write permission on field "
            f"'{field}' but no tool, instruction, or data model references it."
        )


def _check_write_connector_for_readonly_role(node: AgentNode, flow: Flow, result: ValidationResult) -> None:
    """Warning 5: Connector endpoint with write permission enabled for read-only role."""
    if not node.role:
        return

    try:
        from ai_operator import roles as role_mod
        # Check if role has only read access on tools
        tool_access = role_mod.access_for(node.role, "tool:*")
        if tool_access and tool_access[0] == "read":
            # Role is read-only for tools, check if any enabled connector has write level
            for conn in flow.connectors:
                if conn.name in node.tools_enabled:
                    from ai_operator.permissions import PermissionLevel
                    if conn.level in (PermissionLevel.reversible_write.value, PermissionLevel.irreversible_write.value):
                        result.add_warning(
                            f"Agent '{node.node_id}' (role: {node.role}) is read-only but "
                            f"has write-level connector '{conn.name}' enabled."
                        )
    except Exception:
        pass


def _check_missing_data_model_for_extraction(node: AgentNode, result: ValidationResult) -> None:
    """Warning 6: Agent has no data model but instructions mention extracting fields."""
    if node.data_model:
        return

    extraction_keywords = [
        "extract", "parse", "read", "field", "amount", "due date", "due_date",
        "vendor", "invoice", "number", "purchase order", "po", "line item"
    ]
    instructions_lower = (node.instructions or "").lower()
    if any(kw in instructions_lower for kw in extraction_keywords):
        result.add_warning(
            f"Agent '{node.node_id}' has no data model attached but its instructions "
            "mention extracting fields. Consider attaching a data model for validation."
        )


def _role_errors(flow: Flow) -> ValidationResult:
    """Phase 6: an agent's declared role must exist in the roles table."""
    result = ValidationResult()
    wanted = {node.role for node in flow.nodes
              if isinstance(node, AgentNode) and node.role}
    if not wanted:
        return result
    try:
        from platform.dashboard import db

        known = {r["name"] for r in db.list_roles()}
    except Exception:
        return result
    if not known:
        return result
    for node in flow.nodes:
        if isinstance(node, AgentNode) and node.role and node.role not in known:
            result.add_error(
                f"Agent node '{node.node_id}' declares role '{node.role}', "
                "which does not exist in the roles table."
            )
    return result


def _skill_errors(flow: Flow) -> ValidationResult:
    """Phase 5: every skill an agent attaches must exist in the library."""
    result = ValidationResult()
    wanted = {name for node in flow.nodes if isinstance(node, AgentNode)
              for name in (node.skills or [])}
    if not wanted:
        return result
    try:
        from platform.dashboard import db

        known = {s["name"] for s in db.list_skills()}
    except Exception:
        known = set()
    for node in flow.nodes:
        if isinstance(node, AgentNode):
            for name in (node.skills or []):
                if name not in known:
                    result.add_error(
                        f"Agent node '{node.node_id}' references skill "
                        f"'{name}', which does not exist in the skills library."
                    )
    return result


def _connector_errors(flow: Flow) -> ValidationResult:
    """Phase 3: connectors must be unique, usable over HTTP, and not collide
    with built-in registry tools (compilation registers them as tools)."""
    result = ValidationResult()
    seen: set[str] = set()
    from ai_operator.tools import connectors as connector_tools

    registered = set(names())
    builtin = registered - connector_tools.registered_names()
    for conn in flow.connectors:
        if conn.name in seen:
            result.add_error(f"Duplicate connector name '{conn.name}'.")
        seen.add(conn.name)
        if conn.name in builtin:
            result.add_error(
                f"Connector '{conn.name}' collides with a built-in tool; "
                "pick another name."
            )
        method = str(conn.method).upper()
        if method not in SUPPORTED_HTTP_METHODS:
            result.add_error(
                f"Connector '{conn.name}' has unsupported HTTP method "
                f"'{conn.method}' (supported: {', '.join(SUPPORTED_HTTP_METHODS)})."
            )
        if conn.level not in PermissionLevel._value2member_map_:
            result.add_error(
                f"Connector '{conn.name}' has invalid permission level "
                f"'{conn.level}' (read, reversible_write, irreversible_write)."
            )
        if "{{" in conn.url and "}}" not in conn.url:
            result.add_error(f"Connector '{conn.name}' has an unterminated URL template.")
    return result


def _template_variable_errors(flow: Flow, node_map: dict) -> ValidationResult:
    """A message's {{vars.x}} must be defined by an agent visited before it."""
    result = ValidationResult()
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
                    result.add_error(
                        f"Message node '{node.node_id}' references variable "
                        f"'{var}', which no earlier agent node saves."
                    )
        queue.extend(Flow.next_ids(node))
    return result
