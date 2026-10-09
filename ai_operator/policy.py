"""Permissions, financial guardrails and approval routing (deterministic code).

The model never decides whether an action is safe. This module decides
whether a tool call needs a human approval before it runs.
"""

from __future__ import annotations

from ai_operator.models import ApprovalRecord, ApprovalRule, FinancialPolicy
from ai_operator.tools import Tool


def default_policy(auto_threshold: float = 10_000.0, approval_threshold: float = 50_000.0, currency: str = "INR") -> FinancialPolicy:
    return FinancialPolicy(
        currency=currency,
        approval_threshold=approval_threshold,
        rules=[
            ApprovalRule(up_to=auto_threshold, action="auto"),
            ApprovalRule(up_to=approval_threshold, action="manager_approval"),
            ApprovalRule(up_to=None, action="finance_approval"),
        ],
    )


def amount_from_args(args: dict) -> float | None:
    """A financial amount is only an explicit `amount` argument.

    A form field's `value` (e.g. browser_fill field='amount') is not money and
    must not trip the guardrail.
    """
    raw = args.get("amount")
    if raw is None:
        return None
    try:
        cleaned = str(raw).replace(",", "").replace("₹", "").replace("INR", "").strip()
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def should_approve(tool: Tool, args: dict, policy: FinancialPolicy | None = None) -> tuple[bool, str]:
    """Return (needs_approval, reason) for a proposed tool call.

    Read-only tools never need approval — a policy/lookup check with an amount
    argument must not make the human approve *reading* (the safety table says
    read runs automatically). The amount guardrail gates writes only.
    """
    policy = policy or default_policy()
    amount = amount_from_args(args)
    tier = ""
    if amount is not None:
        tier = f"; amount {amount:,.2f} {policy.currency} requires {policy.rule_for(amount).action}"
    if tool.spec.permission == "irreversible_write":
        return True, f"{tool.spec.name} performs an irreversible write{tier}"
    if (amount is not None and tool.spec.permission != "read"
            and policy.requires_approval(amount)):
        return True, f"amount {amount:,.2f} {policy.currency} requires {policy.rule_for(amount).action}"
    return False, ""


def build_approval(tool: Tool, args: dict, policy: FinancialPolicy | None = None) -> ApprovalRecord:
    policy = policy or default_policy()
    _, reason = should_approve(tool, args, policy)
    amount = amount_from_args(args)
    return ApprovalRecord(
        description=f"Approve {tool.spec.name}",
        policy=reason,
        amount=amount,
        currency=policy.currency,
    )