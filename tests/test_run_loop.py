"""Clarification-loop regressions.

The live transcript bug: the model asked "Do you approve …?" via ask_human,
the human answered "yes", and the model asked again (twice), never acting —
burning steps while the UI showed repeated Clarification cards.

These tests pin the three layers of the fix:
1. the prompt carries HUMAN STATUS (approvals + Q&A) and a HUMAN JUST ANSWERED
   nudge,
2. a deterministic guard stops the run when the model re-asks anyway,
3. the dashboard renders approval/answer records distinctly (no duplicate
   "Checked the payment policy" line).
"""

from __future__ import annotations

from ai_operator.llm import StubModel
from ai_operator.models import Decision
from ai_operator.runtime import Operator, _similar


def _ask(question: str) -> Decision:
    return Decision(thought="need input", action_type="ask_human",
                    tool_args={"question": question})


def _drive(op: Operator, task: str, answers: list[str]) -> tuple[dict, int]:
    """Run to completion, answering interrupts; returns (result, resumes_used)."""
    result = op.start(task)
    resumes = 0
    while Operator.suspended(result) and resumes <= len(answers) + 2:
        result = op.resume(result["run_id"], answers[resumes])
        resumes += 1
    return result, resumes


def test_similar_reask_after_answer_is_blocked(tmp_path):
    """Same question asked again right after the human answered -> run stops."""
    script = [
        _ask("Do you approve proceeding with payment for the 120000 invoice?"),
        _ask("Do you approve proceeding with payment for the 120000 invoice?"),
    ]
    op = Operator(model=StubModel(script), storage_dir=str(tmp_path))
    result, resumes = _drive(op, "Check policy for a 120000 invoice", ["yes"])

    assert not Operator.suspended(result), "run must not stay stuck on a re-ask"
    assert resumes == 1, "the second identical question must be blocked immediately"
    report = result["report"]
    assert report["status"] == "completed"          # read-only task, nothing changed
    assert "already answered" in report["summary"]  # honest stop message shown
    op.close()


def test_third_consecutive_question_is_blocked(tmp_path):
    """Even with different wording, two questions in a row with no action stop the run."""
    script = [
        _ask("What is the vendor name for this invoice?"),
        _ask("Which purchase order should I match against?"),
        _ask("What due date should I use when booking it?"),
    ]
    op = Operator(model=StubModel(script), storage_dir=str(tmp_path))
    result, resumes = _drive(op, "Prepare the payment", ["yes", "yes"])

    assert not Operator.suspended(result)
    assert resumes == 2, "the third question in a row must be blocked"
    assert "already answered" in result["report"]["summary"]
    op.close()


def test_prompt_carries_approvals_and_answers(tmp_path):
    """The model must SEE granted approvals and Q&A so it acts instead of re-asking."""
    op = Operator(model=StubModel([]), storage_dir=str(tmp_path))
    state = {
        "run_id": "r1", "request": "Check policy for a 120000 invoice",
        "plan": "", "observation": "Human: yes", "step": 3,
        "approvals": [{"description": "Approve policy_check",
                       "policy": "amount 120,000.00 INR requires finance_approval",
                       "decision": True}],
        "variables": {
            "_qa_log": [{"question": "Proceed with the payment?", "answer": "yes"}],
            "_ask_streak": 1, "_last_question": "Proceed with the payment?",
            "_last_answer": "yes",
        },
    }
    prompt = op._build_prompt(state)
    assert "# HUMAN STATUS" in prompt
    assert "APPROVED" in prompt
    assert "Proceed with the payment?" in prompt
    assert "never re-request an approval" in prompt
    # human just answered -> the hard "act now" nudge
    assert "# HUMAN JUST ANSWERED" in prompt
    assert "Do NOT ask another question" in prompt
    # internal loop-guard bookkeeping stays out of the Variables JSON dump
    assert "_ask_streak" not in prompt.split("Variables:")[1].split("\n")[0]
    op.close()


def test_tool_decision_resets_ask_streak(tmp_path):
    """Progress (a tool call) clears the streak so a genuinely new question later is fine."""
    op = Operator(model=StubModel([]), storage_dir=str(tmp_path))
    vars_ = {"_ask_streak": 2, "_last_question": "q", "_last_answer": "a"}
    d = Decision(thought="look it up", action_type="tool_call",
                 tool_name="vendor_lookup", tool_args={"vendor": "Acme Corp"})
    d, vars_, blocked = op._apply_ask_guard({"run_id": "r"}, d, 1, vars_)
    assert blocked is None and vars_["_ask_streak"] == 0
    op.close()


def test_prompt_carries_recent_step_results(tmp_path):
    """Facts from earlier steps must stay in the prompt — losing them made the
    model re-read the invoice it had just read, tripping the loop guard."""
    op = Operator(model=StubModel([]), storage_dir=str(tmp_path))
    state = {
        "run_id": "r1", "request": "Process the invoice", "plan": "",
        "observation": "vendor lookup result", "step": 4, "approvals": [],
        "variables": {},
        "records": [
            {"step": 2, "summary": "read_file -> Invoice: INV-003, Amount: Rs. 42,500, Due: 2026-07-30"},
            {"step": 3, "summary": "vendor_lookup -> status=active, terms=NET30"},
        ],
    }
    prompt = op._build_prompt(state)
    assert "# RECENT STEPS" in prompt
    assert "INV-003" in prompt and "42,500" in prompt
    assert "never redo these" in prompt
    op.close()


def test_similar_helper():
    assert _similar("Do you approve the payment?", "Do you approve the payment for Acme?")
    assert not _similar("What is the vendor?", "Which due date should I use?")
    assert not _similar("", "")
