"""Understand node: one model call producing understanding + initial plan."""

from __future__ import annotations

from ai_operator.llm import ModelClient, estimated_cost_usd, take_usage
from ai_operator.prompt_loader import extract_json, load_prompt
from ai_operator.state import AgentState
from ai_operator.tracing import trace_event


def _tokens(updates: dict, state: AgentState, usage: dict | None,
            node: str | None = None) -> None:
    """Accumulate one model call's token usage into the run, and trace it."""
    if not usage:
        return
    inp = int(usage.get("input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    model = usage.get("model")
    updates["total_input_tokens"] = state.get("total_input_tokens", 0) + inp
    updates["total_output_tokens"] = state.get("total_output_tokens", 0) + out
    updates["model_name"] = model
    trace_event(state["run_id"], "model_call", node=node, model=model,
                input_tokens=inp, output_tokens=out,
                estimated_cost_usd=estimated_cost_usd(model, inp, out))


def run_understand(state: AgentState, client: ModelClient,
                   agent_node: str | None = None) -> dict:
    task = state["task"]
    understanding = ""
    plan = ""
    system = load_prompt("understand")
    user = f"[TASK]\n{task}\n\n[PROCEDURES]\n{_procedures()}"
    raw = client.complete(system, user)
    usage = take_usage(client)
    try:
        parsed = extract_json(raw)
        understanding = str(parsed.get("understanding", "")).strip()
        plan = str(parsed.get("plan", "")).strip()
        if not understanding or not plan:
            raise ValueError("missing understanding or plan")
    except (ValueError, TypeError) as exc:
        raw = client.complete(
            system,
            f"{user}\n\n[SCHEMA ERROR]\n{exc}\nReturn only the corrected JSON object.",
        )
        retry_usage = take_usage(client)
        if retry_usage:
            usage = _merge_usage(usage, retry_usage)
        try:
            parsed = extract_json(raw)
            understanding = str(parsed.get("understanding", raw)).strip()
            plan = str(parsed.get("plan", "")).strip()
        except (ValueError, TypeError):
            trace_event(state["run_id"], "understand_failed", raw=raw[:500])
            updates = {
                "understanding": understanding or raw[:500],
                "plan": None,
                "status": "needs_human",
                "errors": ["understand step could not parse the model's output"],
            }
            _tokens(updates, state, usage, agent_node)
            return updates
    trace_event(state["run_id"], "understand", understanding=understanding, plan=plan)
    updates = {"understanding": understanding, "plan": plan}
    _tokens(updates, state, usage, agent_node)
    return updates


def _merge_usage(a: dict | None, b: dict) -> dict:
    if not a:
        return b
    return {
        "model": b.get("model") or a.get("model"),
        "input_tokens": int(a.get("input_tokens", 0)) + int(b.get("input_tokens", 0)),
        "output_tokens": int(a.get("output_tokens", 0)) + int(b.get("output_tokens", 0)),
    }


def _procedures() -> str:
    from ai_operator.tools.files import WORKSPACE

    path = WORKSPACE / "company_data" / "procedures.md"
    return path.read_text() if path.exists() else "(no procedures.md found)"
