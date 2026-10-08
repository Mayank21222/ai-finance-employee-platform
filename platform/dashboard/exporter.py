"""Phase 6 section 9: tenant export and import - the no-lock-in answer.

``export_zip(tenant)`` packs everything a company needs to leave: the flow
config, agent version history, skills, data models, roles (+permissions),
connectors with **secrets redacted**, sessions, runs, and each run's trace
files and evidence. ``import_zip(file)`` restores the archive into an empty
database (it refuses to overwrite a database that already has runs).

Deliberately NOT exported: API keys (credentials, hashes are useless
anyway), notification channels/approval links (secrets and runtime state),
and trigger secrets. The manifest records what was omitted and redacted.

Redaction policy for connectors: header values whose name looks like a
credential (auth/token/secret/key/pass/bearer/cookie) become
``***REDACTED***``. The restored connector still works after the company
re-enters its own secret.
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from ai_operator.datamodel import MODELS_PATH, invalidate as invalidate_models
from ai_operator.graph import DEFAULT_FLOW_PATH
from ai_operator.tracing import RUNS_ROOT
from platform.dashboard import db
from platform.flow.models import Flow
from platform.flow.validator import validate

REDACTED = "***REDACTED***"
SENSITIVE_HEADER_RE = re.compile(
    r"auth|token|secret|key|pass|bearer|cookie", re.IGNORECASE)

OMITTED = ["api_keys", "channels", "notifications", "approval_links",
           "trigger secrets"]


def _redact_headers(headers: dict[str, Any]) -> dict[str, Any]:
    return {k: (REDACTED if SENSITIVE_HEADER_RE.search(str(k)) else v)
            for k, v in (headers or {}).items()}


def _export_connectors() -> list[dict[str, Any]]:
    out = []
    for row in db.list_connectors():
        out.append({
            "name": row.get("name"),
            "base_url": row.get("base_url"),
            "headers": _redact_headers(row.get("headers") or {}),
            "endpoints": row.get("endpoints") or [],
        })
    return out


def _zjson(z: zipfile.ZipFile, name: str, payload: Any) -> None:
    z.writestr(name, json.dumps(payload, indent=2, default=str))


def export_zip(tenant: str) -> bytes:
    """Build the tenant archive. Raises ValueError for an unknown tenant."""
    sessions = [s for s in db.list_sessions()
                if str(s.get("tenant")) == tenant]
    if not sessions:
        raise ValueError(f"no sessions for tenant '{tenant}'")
    session_ids = {int(s["id"]) for s in sessions}
    runs = [r for r in db.list_runs(100_000)
            if r.get("session_id") in session_ids]

    roles = [{"name": r["name"], "description": r.get("description", "")}
             for r in db.list_roles()]
    permissions = [{"role": p.get("role_name"),
                    "resource": p.get("resource"),
                    "access": p.get("access"),
                    "approve_limit_amount": p.get("approve_limit_amount")}
                   for p in db.list_role_permissions()]
    skills = [{"name": s["name"], "description": s.get("description", ""),
               "body": s.get("body", "")} for s in db.list_skills()]
    versions = [{"id": v["id"], "node_id": v["node_id"],
                 "version": v["version"], "config": v["config"],
                 "label": v.get("label", ""),
                 "created_at": v.get("created_at")}
                for v in _all_versions()]

    trace_files: list[Path] = []
    for run in runs:
        run_dir = RUNS_ROOT / str(run["run_id"])
        if run_dir.is_dir():
            trace_files += [p for p in sorted(run_dir.rglob("*"))
                            if p.is_file()]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        counts = {"sessions": len(sessions), "runs": len(runs),
                  "roles": len(roles), "permissions": len(permissions),
                  "skills": len(skills), "agent_versions": len(versions),
                  "trace_files": len(trace_files)}
        _zjson(z, "manifest.json", {
            "product": "comp-ops",
            "kind": "tenant-export",
            "tenant": tenant,
            "exported_at": time.time(),
            "counts": counts,
            "connectors_redacted": "sensitive header values",
            "omitted": OMITTED,
        })
        _zjson(z, "sessions.json", sessions)
        _zjson(z, "runs.json", runs)
        _zjson(z, "roles.json", {"roles": roles, "permissions": permissions})
        _zjson(z, "skills.json", skills)
        _zjson(z, "agent_versions.json", versions)
        _zjson(z, "connectors.json", _export_connectors())
        _zjson(z, "data_sources.json", db.list_data_sources())
        _zjson(z, "saved_queries.json", db.list_saved_queries())
        if DEFAULT_FLOW_PATH.is_file():
            z.writestr("flow.json", DEFAULT_FLOW_PATH.read_text("utf-8"))
        if MODELS_PATH.is_file():
            z.writestr("data_models.json", MODELS_PATH.read_text("utf-8"))
        # trace files + evidence: everything under runs/<run_id>/
        for path in trace_files:
            z.writestr(f"traces/{path.relative_to(RUNS_ROOT)}",
                       path.read_bytes())
    return buf.getvalue()


def _all_versions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for cfg in _flow_dicts():
        node_ids |= {str(n.get("node_id")) for n in cfg.get("nodes") or []
                     if isinstance(n, dict)}
    for node_id in sorted(node_ids):
        out.extend(db.list_versions(node_id))
    return out


def _flow_dicts() -> list[dict[str, Any]]:
    try:
        return [json.loads(DEFAULT_FLOW_PATH.read_text("utf-8"))]
    except (OSError, ValueError):
        return []


def _zjson_read(z: zipfile.ZipFile, name: str, default: Any) -> Any:
    try:
        return json.loads(z.read(name).decode("utf-8"))
    except KeyError:
        return default
    except ValueError as exc:
        raise ValueError(f"{name} in the archive is corrupt: {exc}") from exc


def import_zip(data: bytes) -> dict[str, int]:
    """Restore an export into an EMPTY database (no runs may exist yet).

    Raises ValueError before writing anything when the archive is invalid
    or the database already holds runs. The archive is validated first so
    a corrupt file is reported as such, not as a non-empty database.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        manifest = _zjson_read(z, "manifest.json", {})
        if manifest.get("kind") != "tenant-export":
            raise ValueError("not a comp-ops tenant export (missing or "
                             "wrong manifest.json)")
        flow_raw = z.read("flow.json").decode("utf-8") if "flow.json" \
            in names else None
        if flow_raw is None:
            raise ValueError("archive has no flow.json")
        # validate BEFORE any write: an invalid archive changes nothing
        try:
            flow = Flow.model_validate(json.loads(flow_raw))
        except Exception as exc:
            raise ValueError(f"flow.json is not a valid flow: {exc}") from exc
        errors = validate(flow).errors
        if errors:
            raise ValueError("flow.json failed validation: " +
                             "; ".join(errors))
        sessions = _zjson_read(z, "sessions.json", [])
        runs = _zjson_read(z, "runs.json", [])
        roles = _zjson_read(z, "roles.json", {"roles": [],
                                              "permissions": []})
        skills = _zjson_read(z, "skills.json", [])
        versions = _zjson_read(z, "agent_versions.json", [])
        connectors = _zjson_read(z, "connectors.json", [])
        sources = _zjson_read(z, "data_sources.json", [])
        queries = _zjson_read(z, "saved_queries.json", [])
        models_raw = (z.read("data_models.json").decode("utf-8")
                      if "data_models.json" in names else None)

        if db.list_runs(1):
            raise ValueError(
                "this database already has runs; refusing to restore over "
                "it (import needs an empty database)")

        # ---- database restore (ids preserved where they matter) ----
        db.import_sessions(sessions)
        db.import_runs(runs)
        db.import_roles(roles.get("roles") or [],
                        roles.get("permissions") or [])
        db.import_skills(skills)
        db.import_versions(versions)
        db.import_connectors(connectors)
        db.import_data_sources(sources, queries)

        # ---- files ----
        DEFAULT_FLOW_PATH.write_text(
            json.dumps(json.loads(flow_raw), indent=2) + "\n", encoding="utf-8")
        if models_raw is not None:
            MODELS_PATH.write_text(models_raw, encoding="utf-8")
            invalidate_models()
        trace_files = 0
        for member in names:
            prefix = "traces/"
            if not member.startswith(prefix) or member.endswith("/"):
                continue
            rel = member[len(prefix):]
            if ".." in Path(rel).parts:
                continue  # zip-slip guard
            target = RUNS_ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))
            trace_files += 1
    return {
        "sessions": len(sessions), "runs": len(runs),
        "roles": len(roles.get("roles") or []),
        "skills": len(skills), "agent_versions": len(versions),
        "connectors": len(connectors), "trace_files": trace_files,
    }
