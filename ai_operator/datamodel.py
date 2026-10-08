"""Data models: a separately-configurable concept (Phase 4).

A data model is a named list of fields. Field names, types, required flag and
description are the agent's extraction contract for session variables, and
each field carries write/read agent permissions enforced by the variable write
path. Models live in configs/data_models.json (single source of truth shared by
the compiler, the verifier and the dashboard), never in the agent's prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = REPO_ROOT / "configs" / "data_models.json"

FIELD_TYPES = ("text", "number", "date", "boolean")

VALID_WRITE_AGENTS = ("unrestricted", "none")  # sentinel conveniences


class FieldSpec(BaseModel):
    name: str
    type: str
    required: bool = False
    description: str = ""
    write_agents: list[str] = Field(default_factory=list)
    read_agents: list[str] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _type_known(cls, value: str) -> str:
        if value not in FIELD_TYPES:
            raise ValueError(
                f"field type must be one of {', '.join(FIELD_TYPES)}, got {value!r}"
            )
        return value


class DataModel(BaseModel):
    name: str
    description: str = ""
    fields: list[FieldSpec] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def _unique_fields(cls, value: list[FieldSpec]) -> list[FieldSpec]:
        names = [f.name for f in value]
        if len(names) != len(set(names)):
            raise ValueError("data model has duplicate field names")
        return value


SEED_MODEL = DataModel(
    name="Invoice",
    description=(
        "The invoice record the finance employee extracts from vendor "
        "documents. Field names double as the session variable names the "
        "agent saves."
    ),
    fields=[
        FieldSpec(name="vendor_name", type="text", required=True,
                  description="the vendor company name as written in the invoice",
                  write_agents=["ap_agent"],
                  read_agents=["classifier", "ap_agent", "reminder_agent"]),
        FieldSpec(name="invoice_number", type="text", required=True,
                  description="the vendor's invoice reference; do not invent one",
                  write_agents=["ap_agent"],
                  read_agents=["classifier", "ap_agent", "reminder_agent"]),
        FieldSpec(name="amount", type="number", required=True,
                  description=(
                      "the total invoice value in the session currency, as a "
                      "number without currency symbols"),
                  write_agents=["ap_agent"],
                  read_agents=["classifier", "ap_agent", "reminder_agent"]),
        FieldSpec(name="due_date", type="date", required=True,
                  description="the payment due date, ISO format YYYY-MM-DD",
                  write_agents=["ap_agent"],
                  read_agents=["classifier", "ap_agent", "reminder_agent"]),
        FieldSpec(name="purchase_order_ref", type="text", required=False,
                  description="purchase order reference if the invoice mentions one",
                  write_agents=["ap_agent"],
                  read_agents=["classifier", "ap_agent", "reminder_agent"]),
    ],
)

_CACHE: list[DataModel] | None = None


def invalidate() -> None:
    global _CACHE
    _CACHE = None


def _file() -> Path:
    if not MODELS_PATH.exists():
        save_models([SEED_MODEL])
    return MODELS_PATH


def load_models() -> list[DataModel]:
    """All models from configs/data_models.json (auto-seeded)."""
    global _CACHE
    if _CACHE is None:
        raw = json.loads(_file().read_text(encoding="utf-8"))
        _CACHE = [DataModel.model_validate(m) for m in raw.get("models", [])]
    return list(_CACHE)


def save_models(models: list[DataModel], seed_if_empty: bool = True) -> None:
    """Persist the model list and drop the loaded cache."""
    if not models and seed_if_empty:
        models = [SEED_MODEL]
    body = {"models": [m.model_dump() for m in models]}
    MODELS_PATH.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    invalidate()


def get_model(name: str) -> DataModel | None:
    return next((m for m in load_models() if m.name == name), None)


def field_map(name: str) -> dict[str, FieldSpec]:
    model = get_model(name)
    return {f.name: f for f in model.fields} if model else {}


def prompt_block(name: str) -> str:
    """The structured field list the compiler appends below the instructions.

    The agent is expected to extract these fields from documents and save them
    as session variables using the field names as variable names.
    """
    model = get_model(name)
    if model is None:
        return ""
    lines = []
    for f in model.fields:
        flags = [f.type]
        flags.append("required" if f.required else "optional")
        lines.append(
            f"- {f.name} ({', '.join(flags)}): {f.description}"
        )
    return f"== Data model: {model.name} ==\n" \
           f"Extract these fields and save them as session variables "\
           f"(field names are the variable names):\n" + "\n".join(lines)


def write_permission(node: str, var_name: str,
                     model: str | None = None, role: str | None = None) -> str:
    """Return 'write', 'deny' or '' for a variable write attempt.

    A variable that belongs to the writing agent's data model may only be
    written by an agent listed in that field's write_agents. Session variables
    that are not model fields (task_type, checklist answers) are unrestricted.
    When the agent declares no data model, every configured model is checked,
    so a reader-only agent still cannot write a tracked field.

    Phase 6: when `role` is given, the field's role_permissions row is the
    source of truth (checked before the legacy write_agents list); a read-only
    role is denied even if it somehow appears in a field's write_agents.
    """
    models = load_models()
    if model is not None:
        candidate = get_model(model)
        if candidate is not None:
            models = [candidate]
    for m in models:
        fields = {f.name: f for f in m.fields}
        field = fields.get(var_name)
        if field is None:
            continue
        if role:
            from ai_operator import roles
            reason = roles.field_gate(role, m.name, var_name, "write")
            if reason is not None:
                return "deny"
            return "write"
        if node in field.write_agents:
            return "write"
        return "deny"
    return ""


def valid_field_types() -> tuple[str, ...]:
    return FIELD_TYPES