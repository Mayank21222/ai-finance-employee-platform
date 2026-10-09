"""Regressions for provider resilience and the employee tool allow-list.

- A single Groq 429 used to crash the run with a raw HTTPStatusError; transient
  provider errors are now retried with backoff.
- The Tools checkboxes on the employee config screen were decorative: the
  compiler ignored employee.tools and the model saw/ran every tool anyway.
"""

from __future__ import annotations

import httpx
import pytest

from ai_operator.llm import OpenAICompatibleModel, StubModel
from ai_operator.runtime import Operator


class _FlakyPost:
    """Fails n times with the given status, then returns a valid completion."""

    def __init__(self, fails: int, status: int = 429):
        self.fails = fails
        self.status = status
        self.calls = 0

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        request = httpx.Request("POST", url)
        if self.calls <= self.fails:
            return httpx.Response(self.status, request=request)
        return httpx.Response(200, request=request, json={"choices": [{"message": {
            "content": '{"thought":"ok","action_type":"finish"}'}}]})


def test_429_is_retried_then_succeeds(monkeypatch):
    flaky = _FlakyPost(fails=2, status=429)
    monkeypatch.setattr(httpx, "post", flaky)
    monkeypatch.setattr("ai_operator.llm.time.sleep", lambda _s: None)

    m = OpenAICompatibleModel("test-model", "https://api.groq.com/openai/v1",
                              "key", provider="groq")
    d = m.decide("prompt")
    assert d.action_type == "finish"
    assert flaky.calls == 3, "two transient failures then success"


def test_non_transient_error_is_not_retried(monkeypatch):
    flaky = _FlakyPost(fails=99, status=401)
    monkeypatch.setattr(httpx, "post", flaky)
    monkeypatch.setattr("ai_operator.llm.time.sleep", lambda _s: None)

    m = OpenAICompatibleModel("test-model", "https://api.groq.com/openai/v1",
                              "key", provider="groq")
    with pytest.raises(httpx.HTTPStatusError):
        m.decide("prompt")
    assert flaky.calls == 1, "auth errors must fail fast"


def test_persistent_429_gives_up_after_four_attempts(monkeypatch):
    flaky = _FlakyPost(fails=99, status=429)
    monkeypatch.setattr(httpx, "post", flaky)
    monkeypatch.setattr("ai_operator.llm.time.sleep", lambda _s: None)

    m = OpenAICompatibleModel("test-model", "https://api.groq.com/openai/v1",
                              "key", provider="groq")
    with pytest.raises(httpx.HTTPStatusError):
        m.decide("prompt")
    assert flaky.calls == 4


def test_prompt_only_lists_allowed_tools(tmp_path):
    op = Operator(model=StubModel([]), storage_dir=str(tmp_path),
                  allowed_tools=["vendor_lookup", "policy_check"])
    state = {"run_id": "r1", "request": "check vendor", "plan": "",
             "observation": "", "step": 1, "approvals": [], "variables": {}}
    prompt = op._build_prompt(state)
    tool_list = prompt.split("Available tools:")[1]
    assert "vendor_lookup" in tool_list
    assert "policy_check" in tool_list
    # the executable tool *list* must not contain a disabled tool (the prompt
    # template's prose may still mention tools by name)
    assert "create_payment" not in tool_list, "disabled tool leaked into the tool list"
    assert "not enabled for this employee" in prompt or "does not exist" in prompt
    op.close()


def test_disallowed_tool_does_not_execute(tmp_path):
    op = Operator(model=StubModel([]), storage_dir=str(tmp_path),
                  allowed_tools=["vendor_lookup"])
    assert not op._tool_allowed("create_payment")
    out = op._execute_node({
        "run_id": "r1", "step": 2,
        "decision": {"thought": "pay", "action_type": "tool_call",
                     "tool_name": "create_payment",
                     "tool_args": {"vendor": "Acme Corp", "amount": 42500,
                                   "due_date": "2026-07-30"},
                     "plan_update": None, "expected_outcome": None},
        "records": [], "approvals": [], "variables": {},
    })
    assert "not enabled" in out["observation"]
    assert not out.get("approvals"), "no approval should be requested for a blocked tool"
    op.close()


def test_no_allow_list_means_all_tools(tmp_path):
    op = Operator(model=StubModel([]), storage_dir=str(tmp_path))
    assert op._tool_allowed("create_payment") and op._tool_allowed("anything")
    op.close()


def test_compile_employee_passes_employee_tools(tmp_path):
    from builder.flow import compile_employee, new_employee

    emp = new_employee("Tool Tester", "Accounts Payable")
    emp.tools = ["vendor_lookup", "policy_check"]
    op = compile_employee(emp, "http://127.0.0.1:1", str(tmp_path))
    assert op.allowed_tools == {"vendor_lookup", "policy_check"}
    assert not op._tool_allowed("create_payment")
    op.close()


def test_failed_approved_action_is_not_re_asked(tmp_path, monkeypatch):
    """One payment = one approval card. When an approved action fails and the
    model retries it, the existing grant is reused instead of re-prompting
    (the live run showed 4 approval cards for one payment)."""
    from ai_operator.observability import read_events
    from ai_operator.runtime import Operator

    # dead sandbox so create_payment fails deterministically after approval
    script = [
        {"thought": "pay", "action_type": "tool_call", "tool_name": "create_payment",
         "tool_args": {"vendor": "Acme Corp", "amount": 60000, "due_date": "2026-07-30"}},
        {"thought": "pay again", "action_type": "tool_call", "tool_name": "create_payment",
         "tool_args": {"vendor": "Acme Corp", "amount": 60000, "due_date": "2026-07-30"}},
        {"thought": "done", "action_type": "finish"},
    ]
    op = Operator(model=StubModel(script), storage_dir=str(tmp_path),
                  base_url="http://127.0.0.1:1")
    result = op.start("prepare a payment")
    answers = 0
    while Operator.suspended(result) and answers < 5:
        result = op.resume(result["run_id"], "yes")
        answers += 1
    assert not Operator.suspended(result)
    assert answers == 1, f"expected exactly one approval prompt, got {answers}"

    events = read_events(limit=300, run_id=None)
    names = [e["event"] for e in events]
    assert names.count("approval_requested") == 1
    assert "approval_reused" in names
    # both attempts failed (dead URL) but neither crashed the run
    fails = [e for e in events if e.get("event") == "tool_call" and not e.get("ok")]
    assert len(fails) >= 2
    assert result["report"]["status"] in ("completed", "failed")
    op.close()
