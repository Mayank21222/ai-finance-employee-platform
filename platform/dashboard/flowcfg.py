"""Read and write the shipped flow config, validating before every save."""

from __future__ import annotations

import json

from ai_operator.graph import DEFAULT_FLOW_PATH
from ai_operator.permissions import PermissionLevel
from ai_operator.tools import connectors as connector_tools
from platform.dashboard import db
from platform.flow.models import Flow
from platform.flow.validator import validate

MANAGED_KEY = "managed_by"
MANAGED_VALUE = "dashboard"


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


# --- connectors registry (Phase 4) -----------------------------------------


def mirror_db_connectors() -> list[str]:
    """Project the DB connector registry into the flow config.

    The config's `connectors` section is what compile_flow registers on the
    next run, so every dashboard-managed endpoint must live there too (marked
    managed_by=dashboard so a later page deletion can remove it again).
    CLI-authored connectors without the marker are preserved.
    """
    cfg = load_cfg()
    wanted: dict[str, dict] = {}
    for row in db.list_connectors():
        base = str(row["base_url"]).rstrip("/")
        for ep in row.get("endpoints") or []:
            name = str(ep.get("name") or "")
            if not name:
                continue
            wanted[name] = {
                "name": name,
                "description": str(ep.get("description") or ""),
                "method": str(ep.get("method") or "GET").upper(),
                "url": base + str(ep.get("path") or ""),
                "headers": dict(row.get("headers") or {}),
                "body": str(ep.get("body") or ""),
                "level": str(ep.get("level") or "read"),
                "timeout": 15.0,
                MANAGED_KEY: MANAGED_VALUE,
            }
    kept = [c for c in (cfg.get("connectors") or [])
            if c.get("name") not in wanted
            and c.get(MANAGED_KEY) != MANAGED_VALUE]
    merged = kept + list(wanted.values())
    if merged == (cfg.get("connectors") or []):
        return []
    cfg["connectors"] = merged
    return save_cfg(cfg)


def sync_registry() -> list[str]:
    """Load the config's connectors (DB-managed + CLI) into the tool registry."""
    try:
        flow = load_flow()
    except Exception as exc:
        return [f"flow config unreadable: {exc}"]
    return connector_tools.sync(connector_tools.from_defs(flow.connectors))


def publish_connectors() -> list[str]:
    """DB registry -> flow config -> tool registry. Called at startup and
    after every change on the connectors page so the next run sees them."""
    errors = mirror_db_connectors()
    if not errors:
        errors = sync_registry()
    return errors
