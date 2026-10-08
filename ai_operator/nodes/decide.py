"""Decide node: the single LLM-driven decision step of the graph."""

from __future__ import annotations

from langgraph.types import interrupt
from pydantic import ValidationError

from ai_operator.llm import ModelClient, estimated_cost_usd, take_usage
from ai_operator.prompt_loader import extract_json, load_prompt
from ai_operator.state import AgentState, Decision
from ai_operator.tools.registry import describe_for_prompt
from ai_operator.tracing import trace_event

# Design decision (history window): history lines are NEVER dropped - every
# entry stays in the prompt. Only each entry's detail is compressed to a
# single line (whitespace collapsed) and capped at HISTORY_DETAIL_CHARS, so
# context stays bounded without losing the record of what was done. Truncating
# lines instead made the agent forget completed actions and restart cycles.
HISTORY_DETAIL_CHARS = 220


def _user_prompt(
    state: AgentState,
    agent_instructions: str | None = None,
    documents: list[str] | None = None,
) -> str:
    history = state.get("history") or []
    lines = []
    for i, h in enumerate(history):
        kind = h.get("kind", "?")
        detail = " ".join(str(h.get("detail", "")).split())[:HISTORY_DETAIL_CHARS]
        # tool lines are rendered bare so the prompt reads: 3. read_file(path=..) -> ok: ..
        lines.append(f"{i + 1}. {detail}" if kind == "tool" else f"{i + 1}. {kind}: {detail}")
    history_text = "\n".join(lines) or "(empty)"
    remaining = max(0, _max_steps() - int(state.get("step_count", 0)))
    observation = state.get("last_observation") or "none"
    sections = (
        f"[TASK]\n{state['task']}\n\n"
        f"[UNDERSTANDING]\n{state.get('understanding') or '(not set)'}\n\n"
        f"[PLAN]\n{state.get('plan') or '(not set)'}\n\n"
        f"[TOOLS]\n{describe_for_prompt()}\n\n"
        f"[HISTORY]\n{history_text}\n\n"
        f"[LAST OBSERVATION]\n{observation}\n\n"
        f"[REMAINING STEPS]\n{remaining}"
    )
    # Agent extras go LAST so user-authored text can never shadow the core
    # sections above (the stub and retries scan by section heading).
    if agent_instructions:
        sections += f"\n\n[AGENT INSTRUCTIONS]\n{agent_instructions}"
    if documents:
        sections += "\n\n[DOCUMENTS]\n" + "\n\n".join(documents)
    return sections


def _max_steps() -> int:
    import os

    return int(os.environ.get("MAX_STEPS", "25"))


def run_decide(
    state: AgentState,
    client: ModelClient,
    agent_system: str | None = None,
    agent_instructions: str | None = None,
    documents: list[str] | None = None,
    agent_node: str | None = None,
) -> dict:
    run_id = state["run_id"]
    step = int(state.get("step_count", 0)) + 1
    if step > _max_steps():
        trace_event(run_id, "budget_exhausted", step=step, limit=_max_steps())
        return {
            "step_count": step,
            "status": "failed",
            "errors": [f"step budget exhausted after {_max_steps()} steps"],
            "last_observation": "step budget exhausted; no further decisions allowed",
        }

    system = load_prompt("decide")
    if agent_system:
        # Persona first, decision contract second: the schema rules stay last
        # and still win the prompt.
        system = f"{agent_system}\n\n{system}"
    user = _user_prompt(state, agent_instructions, documents)
    raw = client.complete(system, user)
    usages = [take_usage(client)]
    decision, failures = _validate(raw, client, system, user, usages)
    if decision is None:
        answer = interrupt({
            "kind": "model_decision_request",
            "question": (
                "The model returned invalid JSON twice and no fallback decision is safe. "
                "Paste a valid decision JSON object, or type 'quit' to end the run."
            ),
            "last_output": raw[:500],
        })
        if str(answer).strip().lower() in {"quit", "q", "stop"}:
            return {
                "step_count": step,
                "status": "needs_human",
                "errors": ["model output failed schema validation twice; human ended the run"],
                "model_parse_failures": failures,
            }
        try:
            decision = Decision.model_validate(extract_json(str(answer)))
        except (ValidationError, ValueError):
            return {
                "step_count": step,
                "status": "needs_human",
                "errors": ["model output invalid twice and human reply was not valid JSON"],
                "model_parse_failures": failures,
            }

    updates: dict = {
        "step_count": step,
        "last_decision": decision.model_dump(),
        "model_parse_failures": failures,
        "history": [{
            "step": step,
            "kind": "decision",
            "detail": f"{decision.action_type}"
                      + (f" {decision.tool_name}{decision.tool_args}" if decision.tool_name else "")
                      + f" — {decision.thought}",
            "ok": True,
        }],
    }
    if decision.plan_update:
        updates["plan"] = decision.plan_update
    _record_usage(updates, state, usages, agent_node, step)
    trace_event(
        run_id, "decision", step=step, action_type=decision.action_type,
        tool_name=decision.tool_name, tool_args=decision.tool_args,
        thought=decision.thought, expected_outcome=decision.expected_outcome,
        model_parse_failures=failures,
    )
    return updates


def _record_usage(updates: dict, state: AgentState, usages: list[dict | None],
                  node: str | None, step: int) -> None:
    """Phase 5: fold this step's model calls into the run totals and trace them."""
    for usage in usages:
        if not usage:
            continue
        inp = int(usage.get("input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)
        updates["total_input_tokens"] = (
            updates.get("total_input_tokens", state.get("total_input_tokens", 0))
            + inp
        )
        updates["total_output_tokens"] = (
            updates.get("total_output_tokens", state.get("total_output_tokens", 0))
            + out
        )
        updates["model_name"] = usage.get("model") or state.get("model_name")
        trace_event(state["run_id"], "model_call", step=step, node=node,
                    model=usage.get("model"), input_tokens=inp, output_tokens=out,
                    estimated_cost_usd=estimated_cost_usd(
                        usage.get("model"), inp, out))


def _validate(raw: str, client: ModelClient, system: str, user: str,
              usages: list[dict | None] | None = None) -> tuple[Decision | None, int]:
    failures = 0
    try:
        return Decision.model_validate(extract_json(raw)), failures
    except (ValidationError, ValueError) as exc:
        failures = 1
        reason = str(exc)
    raw = client.complete(
        system,
        f"{user}\n\n[SCHEMA ERROR]\nYour previous output was rejected: {reason}\n"
        "Return the corrected JSON object only.",
    )
    if usages is not None:
        usages.append(take_usage(client))
    try:
        return Decision.model_validate(extract_json(raw)), failures
    except (ValidationError, ValueError):
        return None, failures + 1
