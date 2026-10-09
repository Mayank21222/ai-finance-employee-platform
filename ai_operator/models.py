"""Typed models shared by the runtime, platform and mock environment."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Permission(str, Enum):
    READ = "read"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"


class ActionType(str, Enum):
    TOOL_CALL = "tool_call"
    ASK_HUMAN = "ask_human"
    VERIFY = "verify"
    FINISH = "finish"


class Decision(BaseModel):
    """The single JSON shape the model returns for every decision step."""

    thought: str = Field(description="one or two sentences of reasoning")
    action_type: Literal["tool_call", "ask_human", "verify", "finish"]
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    plan_update: str | None = None
    expected_outcome: str | None = None


class ToolSpec(BaseModel):
    """Registry metadata for a tool."""

    name: str
    description: str
    inputs: str
    outputs: str
    permission: Permission
    risk_level: Literal["low", "medium", "high"]
    requires_approval: bool = False

    model_config = ConfigDict(use_enum_values=True)


class StepRecord(BaseModel):
    """One executed step, written to the run trace."""

    step: int
    action_type: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    observation: Any = None
    ok: bool = True
    ts: float = Field(default_factory=time.time)


class Verification(BaseModel):
    """Deterministic verifier result for one intended outcome."""

    checked: str
    expected: str
    found: str
    matched: bool


class ApprovalRecord(BaseModel):
    """A human approval requested during a run."""

    description: str
    policy: str = ""
    amount: float | None = None
    currency: str = "INR"
    decision: bool | None = None
    by: str = "human"


class FinalReport(BaseModel):
    status: Literal["verified_complete", "completed", "failed", "needs_human"]
    summary: str
    actions: list[str] = Field(default_factory=list)
    verification: Verification | None = None
    evidence: list[str] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Financial policy / guardrails
# --------------------------------------------------------------------------- #


class ApprovalRule(BaseModel):
    """A simple financial guardrail tier."""

    up_to: float | None = Field(None, description="amount ceiling in INR; None = no ceiling")
    action: Literal["auto", "manager_approval", "finance_approval"]


class FinancialPolicy(BaseModel):
    currency: str = "INR"
    approval_threshold: float = 50_000.0
    rules: list[ApprovalRule] = Field(default_factory=list)

    def rule_for(self, amount: float) -> ApprovalRule:
        rules = sorted(self.rules, key=lambda r: (r.up_to is None, r.up_to or 0))
        for rule in rules:
            if rule.up_to is None or amount < rule.up_to:
                return rule
        return ApprovalRule(action="manager_approval")

    def requires_approval(self, amount: float) -> bool:
        return self.rule_for(amount).action != "auto"


# --------------------------------------------------------------------------- #
# Employee / flow configuration (platform source of truth)
# --------------------------------------------------------------------------- #


class FlowNode(BaseModel):
    id: str
    type: Literal["trigger", "ai", "finance", "logic", "human", "output"]
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class FlowEdge(BaseModel):
    source: str
    target: str
    label: str = ""


class FlowConfig(BaseModel):
    start: str = "start"
    nodes: list[FlowNode]
    edges: list[FlowEdge]


class Employee(BaseModel):
    """The central UI object of the platform."""

    id: str
    name: str
    role: str
    description: str = ""
    instructions: str = ""
    goals: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    model: str = "stub"
    approval_threshold: float = 50_000.0
    auto_threshold: float = 10_000.0
    currency: str = "INR"
    triggers: list[str] = Field(default_factory=list)
    flow: FlowConfig | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class RunRecord(BaseModel):
    id: str
    employee_id: str
    employee_name: str
    task: str
    status: Literal["running", "completed", "failed", "waiting_approval", "interrupted"]
    started: float = Field(default_factory=time.time)
    duration: float | None = None
    cost: float = 0.0
    trace: str | None = None
    report: FinalReport | None = None