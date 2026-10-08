"""Finish node: build the final JSON report. Status comes from verification, never the model."""

from __future__ import annotations

from ai_operator.llm import estimated_cost_usd
from ai_operator.state import AgentState
from ai_operator.tracing import get_logger, trace_event


def run_finish(state: AgentState) -> dict:
    run_id = state["run_id"]
    verification = state.get("verification")
    previous_status = state.get("status", "running")

    if previous_status == "needs_human":
        status = "needs_human"
    elif verification and verification.get("matched"):
        status = "verified_complete"
    elif verification and not verification.get("matched"):
        status = "failed"
    elif previous_status == "failed":
        status = "failed"
    else:
        status = "failed"

    summary = _summary(state, status, verification)
    input_tokens = int(state.get("total_input_tokens", 0) or 0)
    output_tokens = int(state.get("total_output_tokens", 0) or 0)
    model = state.get("model_name")
    report = {
        "run_id": run_id,
        "task": state["task"],
        "status": status,
        "summary": summary,
        "steps": state.get("step_count", 0),
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
            "model": model,
            "estimated_cost_usd": estimated_cost_usd(model, input_tokens,
                                                     output_tokens),
        },
        "actions": [
            h for h in state.get("history", []) if h.get("kind") in {"tool", "approval", "ask"}
        ],
        "verification": verification or {
            "checked": "nothing", "expected": {}, "found": {},
            "matched": False, "details": "verification never ran; completion cannot be claimed",
        },
        "evidence": state.get("evidence", []),
        "approvals": state.get("approvals", []),
        "errors": state.get("errors", []),
    }
    logger = get_logger(run_id)
    if logger is not None:
        report_path = logger.write_report(report)
        report["report_path"] = str(report_path)
    trace_event(run_id, "finish", status=status, summary=summary)
    return {"status": status, "report": report}


def _summary(state: AgentState, status: str, verification: dict | None) -> str:
    actions = [h for h in state.get("history", []) if h.get("kind") == "tool"]
    if status == "verified_complete":
        return (
            f"Completed and verified: {verification['details']} "
            f"({len(actions)} tool actions over {state.get('step_count', 0)} steps)."
        )
    if status == "needs_human":
        reasons = state.get("errors") or ["a human decision is required"]
        return f"Stopped for a human: {reasons[0]}"
    if verification and not verification.get("matched"):
        return f"Failed verification: {verification.get('details')}"
    errors = state.get("errors") or ["run ended without verification"]
    return f"Failed: {errors[0]}"
