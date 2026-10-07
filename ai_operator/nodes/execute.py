"""Execute node: permission gate, approval interrupt, dispatch with bounded retries."""

from __future__ import annotations

import re
import time

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from ai_operator.permissions import PermissionDecision, approval_prompt, evaluate_permission
from ai_operator.session import SessionContext, session_from_config
from ai_operator.state import AgentState, Decision
from ai_operator.tools.registry import UnknownToolError, get, names, run
from ai_operator.tracing import trace_event

MAX_RETRIES = 2  # initial attempt + 2 retries for transient errors only
RETRY_DELAY_SECONDS = 0.5


def run_execute(
    state: AgentState,
    config: RunnableConfig,
    allowed_tools: tuple[str, ...] = (),
) -> dict:
    run_id = state["run_id"]
    step = int(state.get("step_count", 0))
    session: SessionContext = session_from_config(config)
    decision = Decision.model_validate(state["last_decision"])
    tool_name = decision.tool_name or ""

    try:
        spec = get(tool_name)
    except UnknownToolError:
        observation = (
            f"UNKNOWN TOOL {tool_name!r}. Available tools: {', '.join(names())}. "
            "Choose one of these or ask a human."
        )
        trace_event(run_id, "unknown_tool", step=step, tool_name=tool_name)
        return {
            "last_observation": observation,
            "history": [{"step": step, "kind": "error", "detail": f"unknown tool {tool_name}", "ok": False}],
        }

    if allowed_tools and spec.name not in allowed_tools:
        observation = (
            f"TOOL {spec.name!r} is not enabled for this agent. "
            f"Enabled tools: {', '.join(allowed_tools)}. "
            "Do not call it; use an enabled tool or ask a human."
        )
        trace_event(run_id, "tool_disabled", step=step, tool_name=spec.name,
                    scope="agent")
        return {
            "last_observation": observation,
            "history": [{"step": step, "kind": "error",
                         "detail": f"agent disabled tool {spec.name}", "ok": False}],
        }

    if session.tools_enabled and spec.name not in session.tools_enabled:
        observation = (
            f"TOOL {spec.name!r} is not enabled for session {session.tenant!r}. "
            f"Enabled tools: {', '.join(session.tools_enabled)}. "
            "Do not call it; use an enabled tool or ask a human."
        )
        trace_event(run_id, "tool_disabled", step=step, tool_name=spec.name,
                    tenant=session.tenant)
        return {
            "last_observation": observation,
            "history": [{"step": step, "kind": "error",
                         "detail": f"session disabled tool {spec.name}", "ok": False}],
        }

    permission = evaluate_permission(
        spec.name, spec.level, decision.tool_args,
        policy_threshold=session.approval_threshold,
        require_levels=session.approval_trigger_levels,
        amount_check=session.approval_on_amount_over_threshold,
    )
    approval_records: list[dict] = []
    if permission.outcome == "needs_approval":
        answer = interrupt({
            "kind": "approval",
            "question": approval_prompt(spec.name, decision.tool_args, permission.reason),
            "tool_name": spec.name,
            "tool_args": decision.tool_args,
            "reason": permission.reason,
        })
        approved = str(answer).strip().lower() in {"y", "yes", "approve", "approved"}
        approval_records.append({
            "step": step,
            "tool": spec.name,
            "args": decision.tool_args,
            "reason": permission.reason,
            "question": approval_prompt(spec.name, decision.tool_args, permission.reason),
            "decision": "approved" if approved else "denied",
            "answer": str(answer),
        })
        if not approved:
            observation = (
                f"HUMAN DENIED the approval for {spec.name}({decision.tool_args}). "
                "Do not retry it; report the blockage."
            )
            return {
                "approvals": approval_records,
                "last_observation": observation,
                "status": "needs_human",
                "history": [{"step": step, "kind": "approval", "detail": observation, "ok": False}],
            }

    attempts = 0
    result = None
    while attempts <= MAX_RETRIES:
        attempts += 1
        result = run(spec.name, decision.tool_args)
        if result.ok or not result.retryable:
            break
        if attempts <= MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)
    assert result is not None

    output = result.output if result.ok else f"TOOL ERROR: {result.output}"
    detail = (
        f"{spec.name}({ _args_str(decision.tool_args) }) -> "
        f"{'ok' if result.ok else 'ERROR'}: {output[:400]}"
    )
    trace_event(
        run_id, "tool_result", step=step, tool=spec.name, args=decision.tool_args,
        ok=result.ok, attempts=attempts, retryable=result.retryable, output=output[:1000],
    )
    updates: dict = {
        "last_observation": output,
        "history": [{"step": step, "kind": "tool", "detail": detail, "ok": result.ok}],
    }
    if approval_records:
        updates["approvals"] = approval_records
    evidence = _evidence_path(spec.name, output)
    if evidence:
        updates["evidence"] = [evidence]
    if not result.ok:
        updates["errors"] = [detail]
    return updates


def _evidence_path(tool_name: str, output: str) -> str | None:
    if tool_name != "screenshot":
        return None
    m = re.search(r"saved screenshot (\S+)", output)
    return m.group(1) if m else None


def _args_str(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def evaluate_only(tool_name: str, level, tool_args: dict) -> PermissionDecision:
    """Exposed for tests."""
    return evaluate_permission(tool_name, level, tool_args)
