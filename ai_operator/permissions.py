"""Permission levels, policy rules, and approval routing.

The model never decides whether an action is allowed: it proposes, this module
decides. Anything irreversible or above the policy threshold requires a human
approval interrupt before the tool runs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

APPROVAL_THRESHOLD = 50_000.0
"""Invoices above INR 50,000 need human approval (company_data/policies.md)."""


class PermissionLevel(str, Enum):
    read = "read"
    reversible_write = "reversible_write"
    irreversible_write = "irreversible_write"


class PermissionDecision(BaseModel):
    outcome: Literal["allow", "needs_approval", "deny"]
    reason: str = ""


def _amount_in_args(args: dict[str, Any]) -> float | None:
    raw = args.get("amount")
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").replace("₹", "").strip())
    except ValueError:
        return None


def evaluate_permission(
    tool_name: str,
    level: PermissionLevel,
    tool_args: dict[str, Any],
    policy_threshold: float = APPROVAL_THRESHOLD,
    require_levels: tuple[str, ...] = (PermissionLevel.irreversible_write.value,),
    amount_check: bool = True,
) -> PermissionDecision:
    """Route one proposed tool call: allow, require approval, or deny.

    Threshold and triggering levels come from the session context (callers
    pass them in); the defaults reproduce the original policy.
    """
    if tool_name == "ask_human":
        return PermissionDecision(outcome="allow", reason="clarification is always allowed")

    amount = _amount_in_args(tool_args)
    if amount_check and amount is not None and amount > policy_threshold:
        return PermissionDecision(
            outcome="needs_approval",
            reason=(
                f"policy: amount {amount:.2f} exceeds the {policy_threshold:.0f} "
                "INR approval threshold"
            ),
        )

    if level.value in require_levels:
        if level == PermissionLevel.irreversible_write:
            reason = f"tool '{tool_name}' performs an irreversible write"
        else:
            reason = f"tool '{tool_name}' requires approval at level {level.value}"
        return PermissionDecision(outcome="needs_approval", reason=reason)

    if level == PermissionLevel.reversible_write:
        return PermissionDecision(outcome="allow", reason="reversible write")

    return PermissionDecision(outcome="allow", reason="read-only")


def approval_prompt(tool_name: str, tool_args: dict[str, Any], reason: str) -> str:
    """Human-readable approval question shown by the CLI."""
    bits = ", ".join(f"{k}={v}" for k, v in tool_args.items())
    return f"Approve {tool_name}({bits})? {reason} [y/n]"
