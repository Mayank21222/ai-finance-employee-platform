from ai_operator.policy import amount_from_args, default_policy, should_approve
from ai_operator.tools import get_tool


def test_guardrail_tiers():
    p = default_policy()  # auto < 10k, manager < 50k, finance >= 50k
    assert not p.requires_approval(5_000)
    assert p.rule_for(42_500).action == "manager_approval"
    assert p.rule_for(150_000).action == "finance_approval"


def test_irreversible_always_needs_approval():
    need, reason = should_approve(get_tool("browser_submit"), {}, default_policy())
    assert need and "irreversible" in reason


def test_read_tool_with_large_amount_needs_no_approval():
    """Regression from the live transcript: "Check policy for a 120000 invoice"
    asked the human to approve policy_check itself (a read) before the policy
    could be checked. Reads run automatically — only writes are gated."""
    need, _ = should_approve(get_tool("policy_check"), {"amount": 120000}, default_policy())
    assert not need
    need, _ = should_approve(get_tool("vendor_lookup"), {"amount": "9999999"}, default_policy())
    assert not need


def test_small_reversible_write_is_automatic():
    need, _ = should_approve(get_tool("browser_fill"), {"amount": "5000"}, default_policy())
    assert not need


def test_large_amount_needs_approval():
    need, reason = should_approve(get_tool("browser_fill"), {"amount": "42500"}, default_policy())
    assert need and "manager_approval" in reason


def test_form_value_that_looks_like_money_is_not_gated():
    # browser_fill's `value` is a form value, not a financial amount
    need, _ = should_approve(get_tool("browser_fill"),
                             {"field": "amount", "value": "42500"}, default_policy())
    assert not need


def test_irreversible_reason_includes_tier():
    need, reason = should_approve(get_tool("create_payment"), {"amount": "60000"}, default_policy())
    assert need and "irreversible" in reason and "finance_approval" in reason


def test_amount_parsing():
    assert amount_from_args({"amount": "₹42,500"}) == 42500.0
    assert amount_from_args({"amount": "1,20,000 INR"}) == 120000.0
    assert amount_from_args({"value": "1,20,000 INR"}) is None
    assert amount_from_args({}) is None