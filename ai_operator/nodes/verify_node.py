"""Verify node: plain deterministic code reads real app state; the model has no say."""

from __future__ import annotations

import os
from typing import Any

from ai_operator.state import AgentState
from ai_operator.tracing import trace_event
from ai_operator.verifier import check_model_fields, verify_task


def run_verify(state: AgentState, model_fields: list[Any] | None = None,
               model_name: str | None = None) -> dict:
    run_id = state["run_id"]
    step = int(state.get("step_count", 0))
    app_url = os.environ.get("APP_URL", "http://127.0.0.1:8000")
    result = verify_task(state["task"], app_url)
    # Phase 4: the verifier reads the attached data model for field names and
    # types and runs type/format checks over the variables the agent wrote.
    # This never changes the matched/not-matched decision; it is reported
    # alongside it so field problems surface as audit data.
    if model_fields:
        result.model_check = check_model_fields(
            state.get("variables") or {}, model_fields, model_name)
        mc = result.model_check
        if mc:
            result.details = (f"{result.details} | model fields: {mc['details']}"
                              + ("" if mc["matched"] else " (not matched)"))
    payload = result.model_dump()
    trace_event(
        run_id, "verification", step=step, matched=result.matched,
        checked=result.checked, expected=result.expected, found=result.found,
        details=result.details, model_check=payload.get("model_check"),
    )
    observation = (
        f"VERIFICATION {'MATCHED' if result.matched else 'MISMATCH'}: {result.details}. "
        f"expected={result.expected} found={result.found}"
    )
    history_detail = f"verify() -> {'matched' if result.matched else 'mismatch'}: {result.details[:300]}"
    updates: dict = {
        "verification": payload,
        "last_observation": observation,
        "history": [{"step": step, "kind": "tool", "detail": history_detail, "ok": result.matched}],
    }
    if result.matched:
        from ai_operator.tools.browser import capture_evidence

        path = capture_evidence("verified")
        if path:
            updates["evidence"] = [path]
            trace_event(run_id, "evidence_captured", path=path, step=step)
    return updates