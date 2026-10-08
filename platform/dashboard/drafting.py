"""Phase 6 section 4: AI-assisted flow drafting with a human gate.

The company types a plain-English description of the employee they want; the
server asks the LLM for a **flow config JSON only** (schema + current config
+ tool/connector/skill/data-model catalog in the prompt), runs the result
through the validator, and retries exactly once with the error list if it
fails. Whatever comes back is only ever a preview: a side-by-side diff and
an SVG rendering. Nothing touches the shipped config until the human clicks
Apply, and Apply re-validates before writing (validate-before-write, same
helper every other editor page uses) and records an agent version row
labelled "AI draft" for every changed agent.

Role gate: a draft may not enable a tool that the current role (the flow's
``session_context.user_role``) cannot call - that check runs as part of
``check()``, so it participates in the retry and blocks Apply. Field
permissions live in the roles table, not in flow JSON, so a draft cannot
grant them at all.

With ``STUB_MODEL=1`` the canned two-agent draft is returned directly so
tests run offline with no API key.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ai_operator import roles
from ai_operator.llm import get_client
from ai_operator.tools import registry
from platform.dashboard import db
from platform.flow.models import Flow
from platform.flow.validator import validate

MAX_ATTEMPTS = 2  # first try + exactly one retry with the error list


# --- catalogs for the prompt ------------------------------------------------


def _tool_catalog() -> str:
    try:
        lines = [registry.get(n).to_prompt_line() for n in registry.names()]
    except Exception:  # registry empty (import order): say so, do not crash
        lines = []
    return "\n".join(lines) or "(none registered)"


def _aux_catalog() -> str:
    parts: list[str] = []
    try:
        skills = db.list_skills()
        if skills:
            parts.append("Skills:\n" + "\n".join(
                f"- {s['name']}: {s.get('description') or ''}".rstrip(": ")
                for s in skills))
    except Exception:
        pass
    try:
        from ai_operator.datamodel import load_models

        models = load_models()
        if models:
            parts.append("Data models:\n" + "\n".join(
                f"- {m.name}: " + ", ".join(
                    f"{f.name} ({f.type}{'*' if f.required else ''})"
                    for f in m.fields)
                for m in models))
    except Exception:
        pass
    return "\n".join(parts) or "(none)"


def build_prompts(description: str, cfg: dict[str, Any],
                  errors: list[str] | None = None) -> tuple[str, str]:
    """(system, user) for one drafting attempt.

    Carries the flow JSON schema, the current config, the registered tools
    and connectors, skills, data models and the description - and, on a
    retry, the validator's error list from the previous attempt.
    """
    system = (
        "You design flow configs for the Comp Ops platform. Reply with the "
        "complete flow config JSON only - no prose, no markdown fences. The "
        "config must satisfy the provided JSON schema and these rules: every "
        "connection target must exist as a node, start_node_id must name an "
        "existing node, there must be at least one end node, every agent "
        "node needs an outgoing connection, message-node {{vars.x}} "
        "references must be a prior agent's save_as target, and only tools "
        "from the provided catalog may be enabled."
    )
    parts = [
        "Flow JSON schema:",
        json.dumps(Flow.model_json_schema(), indent=2),
        "Current flow config:",
        json.dumps(cfg, indent=2),
        "Available tools and connectors (only these may be enabled):",
        _tool_catalog(),
        _aux_catalog(),
        "Company description of the employee they want:",
        description or "(none given)",
    ]
    if errors:
        parts += [
            "Your previous attempt was rejected with these validation "
            "errors:",
            "\n".join(f"- {e}" for e in errors),
            "Fix every error and reply again with the corrected flow config "
            "JSON only.",
        ]
    return system, "\n\n".join(parts)


# --- the canned offline draft ----------------------------------------------


def canned_draft(description: str, cfg: dict[str, Any]) -> str:
    """A valid two-agent draft (STUB_MODEL=1) so tests run offline.

    Keeps the current session_context so applying it does not clobber the
    company's tenant/currency/threshold/role settings.
    """
    ctx = dict(cfg.get("session_context") or {})
    ctx.setdefault("tenant", "acme")
    ctx.setdefault("currency", "INR")
    ctx.setdefault("approval_threshold", 50000)
    ctx.setdefault("user_role", "finance_operator")
    draft = {
        "name": str(cfg.get("name") or "flow"),
        "start_node_id": "intake_agent",
        "session_context": ctx,
        "nodes": [
            {
                "type": "agent",
                "node_id": "intake_agent",
                "system_prompt": "You are the intake agent of the finance "
                                 "team. Read the request and decide what "
                                 "kind of work it is.",
                "instructions": description or "Classify the request.",
                "tools_enabled": [],
                "next_node_ids": ["specialist_agent"],
                "fallback_next": "end_node",
                "save_as": "task_summary",
            },
            {
                "type": "agent",
                "node_id": "specialist_agent",
                "system_prompt": "You are the specialist. Carry out the "
                                 "classified work carefully.",
                "instructions": "Handle the request saved by intake_agent.",
                "tools_enabled": [],
                "next_node_ids": ["end_node"],
                "fallback_next": "end_node",
            },
            {"type": "end", "node_id": "end_node"},
        ],
        "connectors": list(cfg.get("connectors") or []),
        "max_visits_per_node": int(cfg.get("max_visits_per_node") or 3),
        "max_total_visits": int(cfg.get("max_total_visits") or 40),
    }
    return json.dumps(draft, indent=2)


# --- parsing and checking ---------------------------------------------------


def parse_draft(text: str) -> dict[str, Any] | None:
    """Extract the flow config object from a model reply, or None."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def role_gate(draft: dict[str, Any], role: str) -> list[str]:
    """Blocking errors when the draft enables a tool the role cannot call.

    With no roles data (roles table empty / no DB) nothing is gated, so a
    bare runtime behaves exactly as before.
    """
    if not role:
        return []
    try:
        if not db.role_permission_map():
            return []
    except Exception:
        return []
    errors: list[str] = []

    def gate(tool: str, where: str) -> None:
        try:
            level = registry.get(tool).level.value
        except Exception:
            level = "write"  # unknown/connector tool: require write access
        reason = roles.tool_gate(role, tool, level)
        if reason:
            errors.append(f"{where} enables tool '{tool}' but {reason}.")

    ctx = draft.get("session_context") or {}
    for tool in ctx.get("tools_enabled") or []:
        gate(tool, "The draft's session context")
    for node in draft.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        for tool in node.get("tools_enabled") or []:
            gate(tool, f"Agent '{node.get('node_id')}'")
    return errors


def check(draft: dict[str, Any], role: str = "") -> list[str]:
    """Everything that must pass before a draft may be shown or applied."""
    try:
        flow = Flow.model_validate(draft)
    except Exception as exc:
        return [f"The draft is not a valid flow config: {exc}"]
    errors = list(validate(flow).errors)
    errors += role_gate(draft, role)
    return errors


def changed_agents(old_cfg: dict[str, Any],
                   new_cfg: dict[str, Any]) -> list[tuple[str, dict]]:
    """(node_id, OLD config) for every agent whose config changed.

    The old config is what gets snapshotted as a version row on Apply -
    the same "record what you replaced" convention the panel saves use.
    """
    old_nodes = {n.get("node_id"): n for n in old_cfg.get("nodes") or []
                 if isinstance(n, dict)}
    changed: list[tuple[str, dict]] = []
    for node in new_cfg.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        nid = node.get("node_id")
        previous = old_nodes.get(nid)
        if previous is not None and previous != node:
            changed.append((str(nid), previous))
    return changed


# --- one drafting run -------------------------------------------------------


def _generate(description: str, cfg: dict[str, Any], system: str,
              user: str) -> str:
    """Model call; the stub branch returns the canned draft offline."""
    if os.environ.get("STUB_MODEL", "").strip() in ("1", "true", "yes"):
        return canned_draft(description, cfg)
    return get_client().complete(system, user)


def draft_flow(description: str, cfg: dict[str, Any],
               role: str = "") -> tuple[dict[str, Any] | None, list[str]]:
    """Draft, validate, retry once. Returns (draft, []) or (None, errors).

    On failure the caller applies nothing - the current flow is untouched
    because this function never writes.
    """
    errors: list[str] | None = None
    for _attempt in range(MAX_ATTEMPTS):
        system, user = build_prompts(description, cfg, errors)
        draft = parse_draft(_generate(description, cfg, system, user))
        if draft is None:
            errors = ["The model did not return a flow config as JSON."]
            continue
        errors = check(draft, role)
        if not errors:
            return draft, []
    return None, list(errors or ["No draft was produced."])
