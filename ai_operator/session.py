"""Immutable session context: company-set rules frozen for the whole run.

Session context is set at run start and never changes. It is NOT a field in
LangGraph state (state is the model's writable working memory); nodes read it
from the graph's configurable section instead, so the model cannot tamper with
approval rules mid-run. Session *variables* (mutable working memory) live in
state; this model is the frozen half.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from ai_operator.permissions import APPROVAL_THRESHOLD


class SessionContext(BaseModel):
    """Frozen per-run rules a company configures without touching code."""

    model_config = ConfigDict(frozen=True)

    tenant: str = "acme"
    currency: str = "INR"
    approval_threshold: float = APPROVAL_THRESHOLD
    user_role: str = "finance_operator"
    tools_enabled: tuple[str, ...] = ()
    """Tools allowed for the whole session; () means every registered tool."""
    approval_trigger_levels: tuple[str, ...] = ("irreversible_write",)
    """Permission levels that always require approval regardless of amount."""
    approval_on_amount_over_threshold: bool = True
    """Set False to stop gating on amount and rely on levels only."""


def session_from_config(config: Any) -> SessionContext:
    """Read the frozen SessionContext out of a LangGraph config, if present."""
    if isinstance(config, dict):
        configurable = config.get("configurable") or {}
        ctx = configurable.get("session_context")
        if isinstance(ctx, SessionContext):
            return ctx
    return SessionContext()
