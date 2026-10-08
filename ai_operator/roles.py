"""Phase 6: roles shared by humans and agents.

The truth lives in the dashboard's ``roles`` and ``role_permissions`` tables
(NocoBase: "Every AI action follows the same fine-grained permissions as human
users"). This module is the runtime resolver used by the execute node, the
field-write path and the approval gate.

Resource strings are ``<kind>:<name>`` where kind is ``tool``, ``endpoint``,
``field`` or ``action``; a ``kind:*`` row acts as the fallback for that kind
(exact match wins, then the longest matching wildcard).

Two failure modes, deliberately different:

* No roles database at all (a pure-runtime test, or ``ai_operator`` used
  without the dashboard) -> nothing is gated, so behaviour is unchanged.
* A database with roles, but no row for this role and resource -> denied.
  A role that says nothing about a resource is read-only by default; only the
  dashboard's seeded roles and explicit rows grant write or approve.
"""

from __future__ import annotations

from typing import Any

from ai_operator.permissions import _amount_in_args

RANK = {"none": 0, "read": 1, "write": 2, "approve": 3}

#: tool PermissionLevel -> the level a role must hold to call it
TOOL_REQUIRED_LEVEL = {
    "read": "read",
    "reversible_write": "write",
    "irreversible_write": "write",
}

APPROVE_ACTION = "approve_payment"

DENIED_MESSAGE = "requires a higher role"


def _permission_map() -> dict[str, dict[str, tuple[str, float | None]]] | None:
    """role name -> {resource: (access, approve_limit)}; None = no DB."""
    try:
        from platform.dashboard import db
        return db.role_permission_map()
    except Exception:
        return None


def role_names() -> list[str]:
    try:
        from platform.dashboard import db
        return [r["name"] for r in db.list_roles()]
    except Exception:
        return []


def role_exists(name: str) -> bool:
    return bool(name) and name in role_names()


def access_for(role: str, resource: str) -> tuple[str, float | None] | None:
    """(access, approve_limit) for this role/resource, or None if unmapped.

    Match order: the exact resource, then ``kind:Some.*``, then ``kind:*``.
    """
    perms = _permission_map()
    if perms is None:
        return None
    rows = perms.get(role)
    if not rows:
        return None
    if resource in rows:
        return rows[resource]
    parts = resource.split(":")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ":".join(parts[:i]) + ":*"
        if candidate in rows:
            return rows[candidate]
    return None


def allows(role: str, resource: str, required: str) -> bool:
    """True when the role holds at least `required` on `resource`.

    Unmapped resources are allowed only when the roles database is absent
    (legacy flows); a database that knows about roles denies by default.
    """
    perms = _permission_map()
    if perms is None:
        return True
    found = access_for(role, resource)
    if found is None:
        return False
    return RANK.get(found[0], 0) >= RANK.get(required, 99)


def tool_gate(role: str, tool_name: str, level: str) -> str | None:
    """Block reason when this role may not call this tool, else None."""
    if not role:
        return None
    required = TOOL_REQUIRED_LEVEL.get(str(level), "write")
    perms = _permission_map()
    if not perms:  # no database, or nothing seeded yet
        return None
    if role not in perms:
        return f"unknown role '{role}'"
    found = access_for(role, f"tool:{tool_name}")
    if found is None:
        return f"role '{role}' has no row for tool permissions (mapped roles deny by default)"
    if RANK.get(found[0], 0) < RANK[required]:
        return (f"role '{role}' has {found[0]} access on tools, "
                f"which cannot {required} '{tool_name}'")
    return None


def field_gate(role: str, model: str, field: str, mode: str = "write") -> str | None:
    """Block reason when this role may not `mode` a data model field."""
    if not role:
        return None
    perms = _permission_map()
    if not perms:  # no database, or nothing seeded yet
        return None
    if role not in perms:
        return f"unknown role '{role}'"
    found = access_for(role, f"field:{model}.{field}")
    if found is None:
        return f"role '{role}' has no row for {model}.{field} (mapped roles deny by default)"
    if RANK.get(found[0], 0) < RANK[mode]:
        return (f"role '{role}' has {found[0]} access on {model}.{field}, "
                f"which cannot {mode}")
    return None


def approve_gate(role: str, amount: float | None = None,
                 action: str = APPROVE_ACTION) -> tuple[bool, str]:
    """May `role` approve this action, and within its amount limit?"""
    perms = _permission_map()
    if not perms:  # no roles database / nothing seeded: Phase 3 policy alone
        return True, ""
    found = access_for(role, f"action:{action}")
    if found is None or found[0] != "approve":
        return False, DENIED_MESSAGE
    limit = found[1]
    if limit is not None and amount is not None and float(amount) > limit:
        return False, (f"{DENIED_MESSAGE}: role '{role}' may approve up to "
                       f"{limit:,.0f}, this is {amount:,.0f}")
    return True, ""


def approval_amount(tool_args: dict[str, Any] | None) -> float | None:
    return _amount_in_args(tool_args or {})


def approve_limit(role: str, action: str = APPROVE_ACTION) -> float | None:
    found = access_for(role, f"action:{action}")
    if found is None or found[0] != "approve":
        return None
    return found[1]
