"""Linear SOP compiled from a visual flow. The LangGraph engine stays one
loop; these stages constrain *what* it may skip, pause, and prefer."""

from __future__ import annotations

from typing import Any


def skip_decorative(workflow: list[dict], idx: int) -> int:
    n = len(workflow)
    while idx < n and workflow[idx].get("kind") == "skip":
        idx += 1
    return idx


def bump_stage(workflow: list[dict], idx: int, tool_name: str) -> int:
    """Advance to after the first matching work stage at-or-after idx.

    Unmatched tools do not move the pointer onto a human gate — that pause
    is only reached by completing the work stage before it.
    """
    idx = skip_decorative(workflow, idx)
    n = len(workflow)
    for i in range(idx, n):
        stage = workflow[i]
        if stage.get("kind") == "human":
            return idx
        if stage.get("kind") == "skip":
            continue
        tools = stage.get("tools") or []
        if tool_name in tools:
            return skip_decorative(workflow, i + 1)
    return idx


def current_stage(workflow: list[dict], idx: int) -> dict[str, Any] | None:
    idx = skip_decorative(workflow, idx)
    if 0 <= idx < len(workflow):
        return workflow[idx]
    return None


def sop_prompt(workflow: list[dict], idx: int) -> str:
    if not workflow:
        return ""
    idx = skip_decorative(workflow, idx)
    lines = ["# EMPLOYEE WORKFLOW (designed in the visual builder — this is your SOP)"]
    for i, stage in enumerate(workflow):
        marker = ">> CURRENT" if i == idx else ("done" if i < idx else "later")
        tools = ", ".join(stage.get("tools") or []) or "no specific tool"
        note = (stage.get("instructions") or "").strip()
        extra = f" — {note}" if note else ""
        lines.append(
            f"{i + 1}. [{marker}] {stage.get('name')} ({stage.get('type')}/{stage.get('kind')})"
            f" tools: {tools}{extra}"
        )
    lines.append(
        "Follow remaining CURRENT/later stages in order when they apply to the request. "
        "If the human asked for a narrow lookup and that work is done, you may finish "
        "without completing later SOP stages. Never skip a CURRENT human stage: you must "
        "ask_human (or wait — the runtime will pause) before continuing. "
        "Do not invent tools that are not in Available tools."
    )
    return "\n".join(lines) + "\n"
