"""Understand node: one model call producing understanding + initial plan."""

from __future__ import annotations

from ai_operator.llm import ModelClient
from ai_operator.prompt_loader import extract_json, load_prompt
from ai_operator.state import AgentState
from ai_operator.tracing import trace_event


def run_understand(state: AgentState, client: ModelClient) -> dict:
    task = state["task"]
    understanding = ""
    plan = ""
    system = load_prompt("understand")
    user = f"[TASK]\n{task}\n\n[PROCEDURES]\n{_procedures()}"
    raw = client.complete(system, user)
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
        try:
            parsed = extract_json(raw)
            understanding = str(parsed.get("understanding", raw)).strip()
            plan = str(parsed.get("plan", "")).strip()
        except (ValueError, TypeError):
            trace_event(state["run_id"], "understand_failed", raw=raw[:500])
            return {
                "understanding": understanding or raw[:500],
                "plan": None,
                "status": "needs_human",
                "errors": ["understand step could not parse the model's output"],
            }
    trace_event(state["run_id"], "understand", understanding=understanding, plan=plan)
    return {"understanding": understanding, "plan": plan}


def _procedures() -> str:
    from ai_operator.tools.files import WORKSPACE

    path = WORKSPACE / "company_data" / "procedures.md"
    return path.read_text() if path.exists() else "(no procedures.md found)"
