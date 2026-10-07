"""Read and write the shipped flow config, validating before every save."""

from __future__ import annotations

import json

from ai_operator.graph import DEFAULT_FLOW_PATH
from platform.flow.models import Flow
from platform.flow.validator import validate


def load_cfg() -> dict:
    return json.loads(DEFAULT_FLOW_PATH.read_text())


def save_cfg(cfg: dict) -> list[str]:
    """Validate and persist; returns errors (config untouched when invalid)."""
    try:
        flow = Flow.model_validate(cfg)
    except Exception as exc:
        return [str(exc)]
    errors = validate(flow)
    if errors:
        return errors
    DEFAULT_FLOW_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return []


def load_flow() -> Flow:
    return Flow.model_validate(load_cfg())
