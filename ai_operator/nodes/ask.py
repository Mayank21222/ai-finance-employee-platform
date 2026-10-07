"""Ask-human node: one specific clarifying question via the interrupt primitive."""

from __future__ import annotations

from langgraph.types import interrupt

from ai_operator.state import AgentState, Decision
from ai_operator.tracing import trace_event


def run_ask(state: AgentState) -> dict:
    run_id = state["run_id"]
    step = int(state.get("step_count", 0))
    decision = Decision.model_validate(state["last_decision"])
    question = decision.tool_args.get("question") or decision.expected_outcome or state["task"]
    answer = interrupt({"kind": "clarification", "question": str(question), "step": step})
    observation = f"HUMAN ANSWER: {answer}"
    trace_event(run_id, "clarification", step=step, question=str(question), answer=str(answer))
    return {
        "last_observation": observation,
        "human_message": str(answer),
        "history": [{
            "step": step,
            "kind": "ask",
            "detail": f"ask_human({question}) -> {answer}",
            "ok": True,
        }],
    }
