"""Typed graph state and the model's decision schema.

Everything here must stay JSON-serializable: the SQLite checkpointer stores
whole state values between steps.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


class Decision(BaseModel):
    """Exact JSON shape the decide step must return (validated with Pydantic)."""

    thought: str = Field(description="one or two sentences of reasoning")
    action_type: Literal["tool_call", "ask_human", "verify", "finish"]
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    plan_update: str | None = None
    expected_outcome: str = ""


class HistoryEntry(TypedDict, total=False):
    step: int
    kind: str  # decision | tool | approval | verification | error
    detail: str
    ok: bool


class AgentState(TypedDict, total=False):
    task: str
    run_id: str
    step_count: int
    understanding: str | None
    plan: str | None
    last_decision: dict[str, Any] | None
    last_observation: str | None
    history: Annotated[list[HistoryEntry], operator.add]
    status: Literal["running", "verified_complete", "failed", "needs_human"]
    verification: dict[str, Any] | None
    approvals: Annotated[list[dict[str, Any]], operator.add]
    evidence: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    report: dict[str, Any] | None
    human_message: str | None
    model_parse_failures: int


INITIAL_STATE_KEYS: dict[str, Any] = {
    "step_count": 0,
    "understanding": None,
    "plan": None,
    "last_decision": None,
    "last_observation": None,
    "history": [],
    "status": "running",
    "verification": None,
    "approvals": [],
    "evidence": [],
    "errors": [],
    "report": None,
    "human_message": None,
    "model_parse_failures": 0,
}
