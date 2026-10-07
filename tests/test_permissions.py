"""Permission routing: the model proposes, permissions.py decides."""

from __future__ import annotations

from ai_operator.permissions import (
    PermissionDecision,
    PermissionLevel,
    approval_prompt,
    evaluate_permission,
)


def test_read_is_allowed():
    d = evaluate_permission("read_file", PermissionLevel.read, {"path": "x"})
    assert d.outcome == "allow"
    assert d.reason == "read-only"


def test_reversible_write_is_allowed():
    d = evaluate_permission("fill", PermissionLevel.reversible_write, {"selector": "#a"})
    assert d.outcome == "allow"
    assert d.reason == "reversible write"


def test_irreversible_write_needs_approval():
    d = evaluate_permission(
        "submit_form", PermissionLevel.irreversible_write, {"selector": "form"}
    )
    assert d.outcome == "needs_approval"
    assert "irreversible" in d.reason


def test_amount_above_threshold_needs_approval():
    d = evaluate_permission(
        "fill", PermissionLevel.reversible_write, {"amount": "52,000.00"}
    )
    assert d.outcome == "needs_approval"
    assert "threshold" in d.reason


def test_amount_at_threshold_is_allowed_for_reads():
    d = evaluate_permission("read_file", PermissionLevel.read, {"amount": "50000"})
    assert d.outcome == "allow"


def test_unparseable_amount_falls_back_to_level():
    d = evaluate_permission("fill", PermissionLevel.reversible_write, {"amount": "abc"})
    assert d.outcome == "allow"


def test_ask_human_always_allowed():
    d = evaluate_permission("ask_human", PermissionLevel.irreversible_write, {})
    assert d.outcome == "allow"
    assert "clarification" in d.reason


def test_approval_prompt_includes_tool_and_reason():
    prompt = approval_prompt("submit_form", {"selector": "form"}, "irreversible write")
    assert prompt == "Approve submit_form(selector=form)? irreversible write [y/n]"


def test_decision_is_a_typed_model():
    d = evaluate_permission("read_file", PermissionLevel.read, {})
    assert isinstance(d, PermissionDecision)
    assert d.model_dump() == {"outcome": "allow", "reason": "read-only"}
