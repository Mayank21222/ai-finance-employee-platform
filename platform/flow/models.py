"""Pydantic models for a flow: the JSON a company saves from the dashboard.

A flow is a list of typed nodes (agent, message, verify, end) plus a start
node and the frozen session-context block. Connections live inside the nodes
(agent/message: next_node_ids + fallback_next; verify: match/mismatch next),
so the JSON is what the flow editor mutates directly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from ai_operator.session import SessionContext

TEMPLATE_VAR_RE = re.compile(r"\{\{\s*vars\.(\w+)\s*\}\}")


class AgentNode(BaseModel):
    type: Literal["agent"] = "agent"
    node_id: str = Field(min_length=1)
    system_prompt: str = ""
    """Agent persona: either a prompt file path (prompts/*.md) or inline text."""
    instructions: str = ""
    """Step-by-step instructions shown to this agent as a dedicated section."""
    documents: list[str] = Field(default_factory=list)
    """Paths of company documents attached to this agent."""
    tools_enabled: list[str] = Field(default_factory=list)
    next_node_ids: list[str] = Field(default_factory=list)
    fallback_next: str | None = None
    """Where this node routes when the run fails or a limit is hit."""
    save_as: str | None = None
    """Optional session variable name to save this agent's answer into."""
    routes: dict[str, str] = Field(default_factory=dict)
    """Phase 3: value -> node_id routing keyed on the saved save_as variable.

    When the agent finishes, the flow leaves through routes[variable value]
    (matched exactly, case-insensitively after stripping); an unmatched value
    leaves through next_node_ids/fallback_next as before. This is how the
    Classifier picks a specialist without any code change.
    """
    data_model: str | None = None
    """Phase 4: name of the config-defined data model this agent extracts.

    The compiler appends the model's structured field list below this agent's
    instructions and registers field-level write/read permissions for the
    variable write path.
    """
    skills: list[str] = Field(default_factory=list)
    """Phase 5: names of reusable platform skills attached to this agent.

    The compiler loads each skill's body from the dashboard database and
    appends it below the agent's own instructions and above the data model
    block, each labelled with the skill name.
    """


class MessageNode(BaseModel):
    type: Literal["message"] = "message"
    node_id: str = Field(min_length=1)
    template: str
    """Message text; may contain {{vars.variable_name}} placeholders."""
    next_node_ids: list[str] = Field(default_factory=list)
    fallback_next: str | None = None
    """Routing target when a template variable is not set yet."""


class VerifyNode(BaseModel):
    type: Literal["verify"] = "verify"
    node_id: str = Field(min_length=1)
    check_type: str = "payables_state"
    match_next: str
    mismatch_next: str


class EndNode(BaseModel):
    type: Literal["end"] = "end"
    node_id: str = Field(min_length=1)


FlowNode = Annotated[
    Union[AgentNode, MessageNode, VerifyNode, EndNode], Field(discriminator="type")
]


class SessionContextBlock(BaseModel):
    """The immutable values a company sets once per session (flow side)."""

    model_config = ConfigDict(frozen=True)

    tenant: str
    currency: str
    approval_threshold: float
    user_role: str
    tools_enabled: list[str] = Field(default_factory=list)

    def to_session_context(self) -> SessionContext:
        return SessionContext(
            tenant=self.tenant,
            currency=self.currency,
            approval_threshold=self.approval_threshold,
            user_role=self.user_role,
            tools_enabled=tuple(self.tools_enabled),
        )


class ConnectorDef(BaseModel):
    """Phase 3: an external API tool defined entirely in config.

    name/description/method/url (with {{vars.x}}, {{args.x}}, {{session.<key>}}
    and {{app.base_url}} placeholders), headers, a JSON body template and a
    permission level - no Python needed to reach a company's real API.
    """

    name: str = Field(min_length=1)
    description: str = ""
    method: str = "GET"
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    level: str = "read"
    timeout: float = 15.0


class Flow(BaseModel):
    name: str = "flow"
    start_node_id: str
    session_context: SessionContextBlock
    nodes: list[FlowNode] = Field(default_factory=list)
    connectors: list[ConnectorDef] = Field(default_factory=list)
    """Phase 3: external API connectors exposed as registry tools."""
    max_visits_per_node: int = 3
    """Platform default: how often one flow node may be entered per run."""
    max_total_visits: int = 40
    """Platform default: total flow-node entries allowed per run."""

    def node_map(self) -> dict[str, FlowNode]:
        return {node.node_id: node for node in self.nodes}

    @staticmethod
    def next_ids(node: FlowNode) -> list[str]:
        """Every outgoing connection target of a node."""
        if isinstance(node, (AgentNode, MessageNode)):
            ids = list(node.next_node_ids)
            if node.fallback_next:
                ids.append(node.fallback_next)
            # Route targets are real outgoing connections too: validation and
            # the template-variable BFS must be able to reach them.
            if isinstance(node, AgentNode):
                ids.extend(node.routes.values())
            return ids
        if isinstance(node, VerifyNode):
            return [node.match_next, node.mismatch_next]
        return []

    @staticmethod
    def primary_next(node: FlowNode) -> str | None:
        """Routing target for a completed node: first connection, else fallback."""
        if isinstance(node, (AgentNode, MessageNode)):
            if node.next_node_ids:
                return node.next_node_ids[0]
            return node.fallback_next
        if isinstance(node, VerifyNode):
            return node.match_next
        return None

    @staticmethod
    def template_variables(node: MessageNode) -> list[str]:
        """Variable names a message template references."""
        return TEMPLATE_VAR_RE.findall(node.template)


def load_flow(path: Path | str) -> Flow:
    """Load and validate the shape of a flow JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Flow.model_validate(data)
