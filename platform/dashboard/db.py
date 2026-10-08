"""SQLite storage for dashboard sessions, run history and connectors.

Path override via DASH_DB env var so tests never touch the real file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = _REPO_ROOT / "dashboard.db"

# Phase 4: the shipped example connector (Improvements.md) - the mock
# payables system's read API, shown active in the demo.
SEED_CONNECTOR = {
    "name": "Mock Payables System",
    "base_url": "{{app.base_url}}",
    "headers": {"Accept": "application/json"},
    "endpoints": [{
        "name": "get_invoice_list",
        "description": ("Get invoice list - fetch the current invoice list "
                        "from the accounting API (mock app, read-only)."),
        "method": "GET",
        "path": "/api/invoices?tenant={{session.tenant}}",
        "level": "read",
        "response_fields": "vendor, amount",
    }],
}

# Phase 6: roles shared by humans and agents (Improvements.md section 1).
# The three mandated roles first, then the roles the shipped sessions and
# flows already use so nothing existing has to be renamed.
SEED_ROLES: dict[str, str] = {
    "finance_manager": "Approves payments of any amount; writes every field "
                       "and tool.",
    "ap_clerk": "Accounts-payable clerk: writes invoice fields and tools, "
                "approves small payments.",
    "auditor": "Read-only: reads fields and tools, cannot write or approve.",
    "finance_operator": "Default dashboard session role: writes tools, "
                        "approves up to 1,000,000.",
    "viewer": "Read-only dashboard user: no write, no approval.",
}

# (role, resource, access, approve_limit_amount). `kind:*` is the fallback for
# that kind; `field:Invoice.*` narrows it for the shipped data model.
SEED_ROLE_PERMISSIONS: list[tuple[str, str, str, float | None]] = [
    ("finance_manager", "tool:*", "write", None),
    ("finance_manager", "field:*", "write", None),
    ("finance_manager", "action:approve_payment", "approve", None),
    ("ap_clerk", "tool:*", "write", None),
    ("ap_clerk", "field:*", "read", None),
    ("ap_clerk", "field:Invoice.*", "write", None),
    ("ap_clerk", "action:approve_payment", "approve", 25000.0),
    ("auditor", "tool:*", "read", None),
    ("auditor", "field:*", "read", None),
    ("auditor", "action:approve_payment", "none", None),
    # Phase 6 section 2: starting a run is a named action. A read-only role
    # (auditor, viewer) may hold API keys but cannot start runs.
    ("finance_manager", "action:start_run", "write", None),
    ("ap_clerk", "action:start_run", "write", None),
    ("auditor", "action:start_run", "none", None),
    ("finance_operator", "action:start_run", "write", None),
    ("viewer", "action:start_run", "none", None),
    ("finance_operator", "tool:*", "write", None),
    ("finance_operator", "field:*", "write", None),
    ("finance_operator", "action:approve_payment", "approve", 1000000.0),
    ("viewer", "tool:*", "read", None),
    ("viewer", "field:*", "read", None),
]


def db_path() -> Path:
    return Path(os.environ.get("DASH_DB") or DEFAULT_DB)


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant TEXT NOT NULL,
                currency TEXT NOT NULL,
                approval_threshold REAL NOT NULL,
                user_role TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                session_id INTEGER,
                task TEXT,
                status TEXT NOT NULL,
                error TEXT,
                created_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS connectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                headers TEXT NOT NULL DEFAULT '{}',
                endpoints TEXT NOT NULL DEFAULT '[]',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                config TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                task TEXT NOT NULL,
                cron TEXT NOT NULL,
                tz_offset REAL NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_at REAL,
                last_run_id TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS role_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role_id INTEGER NOT NULL REFERENCES roles(id)
                    ON DELETE CASCADE,
                resource TEXT NOT NULL,
                access TEXT NOT NULL DEFAULT 'none',
                approve_limit_amount REAL,
                UNIQUE(role_id, resource)
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                role_id INTEGER NOT NULL REFERENCES roles(id),
                created_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                task_template TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_fired_at REAL,
                last_run_id TEXT,
                last_file_path TEXT,
                secret TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            """
        )
        # Phase-3 state machine column (queued|running|waiting_approval|
        # completed|failed|interrupted); migrate pre-existing tables.
        try:
            con.execute("ALTER TABLE runs ADD COLUMN state TEXT "
                        "NOT NULL DEFAULT 'interrupted'")
        except sqlite3.OperationalError:
            pass
        # Phase 5: agent version numbers used by each run + run source.
        for ddl in (
            "ALTER TABLE runs ADD COLUMN agent_versions_used TEXT",
            "ALTER TABLE runs ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
            "ALTER TABLE runs ADD COLUMN schedule_id INTEGER",
            "ALTER TABLE runs ADD COLUMN skipped_reason TEXT",
        ):
            try:
                con.execute(ddl)
            except sqlite3.OperationalError:
                pass
        row = con.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        if row["n"] == 0:
            con.execute(
                "INSERT INTO sessions (tenant, currency, approval_threshold, "
                "user_role, created_at) VALUES (?, ?, ?, ?, ?)",
                ("acme", "INR", 50000.0, "finance_operator", time.time()),
            )
        row = con.execute("SELECT COUNT(*) AS n FROM connectors").fetchone()
        if row["n"] == 0:
            con.execute(
                "INSERT INTO connectors (name, base_url, headers, endpoints, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (SEED_CONNECTOR["name"], SEED_CONNECTOR["base_url"],
                 json.dumps(SEED_CONNECTOR["headers"]),
                 json.dumps(SEED_CONNECTOR["endpoints"]), time.time()),
            )
        _seed_roles(con)


def _seed_roles(con: sqlite3.Connection) -> None:
    """Phase 6: seed roles once, then migrate Phase 4 field permissions.

    Both halves run only while their table is empty, so a permission a human
    later changes on the matrix page is never silently re-granted on restart.
    The migration reads the stored per-field ``write_agents``/``read_agents``
    and the agent's ``role`` in the flow config and turns them into rows -
    after this point the roles table, not the agent list, is the source.
    """
    if con.execute("SELECT COUNT(*) AS n FROM roles").fetchone()["n"] == 0:
        now = time.time()
        for name, description in SEED_ROLES.items():
            con.execute(
                "INSERT INTO roles (name, description, created_at) "
                "VALUES (?, ?, ?)", (name, description, now))
    if con.execute("SELECT COUNT(*) AS n FROM role_permissions").fetchone()["n"]:
        return
    role_ids = {r["name"]: r["id"] for r in list_roles(con=con)}
    grants: dict[tuple[str, str], tuple[str, float | None]] = {}
    for role, resource, access, limit in SEED_ROLE_PERMISSIONS:
        grants[(role, resource)] = (access, limit)
    grants.update(_migrated_field_grants())
    for (role, resource), (access, limit) in grants.items():
        role_id = role_ids.get(role)
        if role_id is None:
            continue
        con.execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, resource, "
            "access, approve_limit_amount) VALUES (?, ?, ?, ?)",
            (role_id, resource, access, limit))


def _migrated_field_grants() -> dict[tuple[str, str], tuple[str, float | None]]:
    """Phase 4 field write/read agent lists, expressed as role permissions."""
    grants: dict[tuple[str, str], tuple[str, float | None]] = {}
    try:
        from ai_operator import datamodel
        from platform.flow.models import load_flow
        roles_by_node = {n.node_id: n.role for n in load_flow().nodes
                         if getattr(n, "role", "")}
        models = datamodel.load_models()
    except Exception:
        return grants
    rank = {"read": 1, "write": 2}
    for model in models:
        for field in model.fields:
            for access, agents in (("write", field.write_agents),
                                   ("read", field.read_agents)):
                for agent in agents:
                    role = roles_by_node.get(agent)
                    if not role:
                        continue
                    key = (role, f"field:{model.name}.{field.name}")
                    if rank[access] >= rank.get(grants.get(key, ("none",))[0], 0):
                        grants[key] = (access, None)
    return grants


def list_sessions() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM sessions ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_session(session_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return dict(row) if row else None


def add_session(tenant: str, currency: str, threshold: float,
                user_role: str) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO sessions (tenant, currency, approval_threshold, "
            "user_role, created_at) VALUES (?, ?, ?, ?, ?)",
            (tenant, currency, threshold, user_role, time.time()),
        )
        return int(cur.lastrowid)


def delete_session(session_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def record_run(run_id: str, session_id: int, task: str, source: str = "manual",
               schedule_id: int | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, session_id, task, status, "
            "error, created_at, finished_at, state, source, schedule_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, session_id, task, "running", None, time.time(), None,
             "queued", source, schedule_id),
        )


def record_skipped_run(run_id: str, session_id: int, task: str, reason: str,
                       source: str = "schedule",
                       schedule_id: int | None = None) -> None:
    """Phase 5: a schedule that was due but could not fire.

    Recorded as a finished, skipped run so the reason is visible in history
    instead of silently dropped.
    """
    now = time.time()
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, session_id, task, status, "
            "error, created_at, finished_at, state, source, schedule_id, "
            "skipped_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, session_id, task, "skipped", None, now, now, "skipped",
             source, schedule_id, reason),
        )


def set_state(run_id: str, state: str) -> None:
    with connect() as con:
        con.execute("UPDATE runs SET state = ? WHERE run_id = ?",
                    (state, run_id))


def finish_run(run_id: str, status: str, error: str | None = None,
               state: str = "completed") -> None:
    with connect() as con:
        con.execute(
            "UPDATE runs SET status = ?, error = ?, state = ?, finished_at = ? "
            "WHERE run_id = ?",
            (status, error, state, time.time(), run_id),
        )


def orphaned_states() -> list[dict[str, Any]]:
    """Runs stuck in queued/running whose live handle is gone (crashed)."""
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM runs WHERE state IN ('queued', 'running')"
        ).fetchall()
    return [dict(r) for r in rows]


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT r.*, s.tenant FROM runs r "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "ORDER BY r.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "SELECT r.*, s.tenant FROM runs r "
            "LEFT JOIN sessions s ON s.id = r.session_id "
            "WHERE r.run_id = ?",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


# --- connectors registry (Phase 4) -----------------------------------------


def _connector_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["headers"] = json.loads(data.get("headers") or "{}")
    except ValueError:
        data["headers"] = {}
    try:
        data["endpoints"] = json.loads(data.get("endpoints") or "[]")
    except ValueError:
        data["endpoints"] = []
    return data


def list_connectors() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM connectors ORDER BY id").fetchall()
    return [_connector_row(r) for r in rows]


def get_connector(connector_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM connectors WHERE id = ?",
                          (connector_id,)).fetchone()
    return _connector_row(row) if row else None


def add_connector(name: str, base_url: str, headers: dict[str, str],
                  endpoints: list[dict[str, Any]] | None = None) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO connectors (name, base_url, headers, endpoints, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (name, base_url, json.dumps(headers),
             json.dumps(endpoints or []), time.time()),
        )
        return int(cur.lastrowid)


def delete_connector(connector_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM connectors WHERE id = ?", (connector_id,))


def add_endpoint(connector_id: int, endpoint: dict[str, Any]) -> None:
    row = get_connector(connector_id)
    if row is None:
        raise KeyError(connector_id)
    endpoints = list(row["endpoints"]) + [endpoint]
    with connect() as con:
        con.execute("UPDATE connectors SET endpoints = ? WHERE id = ?",
                    (json.dumps(endpoints), connector_id))


def delete_endpoint(connector_id: int, endpoint_name: str) -> bool:
    row = get_connector(connector_id)
    if row is None:
        return False
    endpoints = [e for e in row["endpoints"] if e.get("name") != endpoint_name]
    if len(endpoints) == len(row["endpoints"]):
        return False
    with connect() as con:
        con.execute("UPDATE connectors SET endpoints = ? WHERE id = ?",
                    (json.dumps(endpoints), connector_id))
    return True


# --- agent configuration version history (Phase 5) --------------------------


def add_version(node_id: str, config: dict[str, Any],
                label: str = "") -> int:
    """Append an immutable snapshot; versions are never deleted or edited.

    The caller snapshots the configuration being REPLACED (the old one), so
    every state the agent has ever held is materialized exactly once in the
    history. Returns the new version number.
    """
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM agent_versions "
            "WHERE node_id = ?", (node_id,)).fetchone()
        version = int(row["v"]) + 1
        con.execute(
            "INSERT INTO agent_versions (node_id, version, config, label, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (node_id, version, json.dumps(config), label, time.time()),
        )
        return version


def list_versions(node_id: str) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM agent_versions WHERE node_id = ? "
            "ORDER BY version DESC", (node_id,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["config"] = json.loads(item["config"])
        except ValueError:
            item["config"] = {}
        out.append(item)
    return out


def get_version(node_id: str, version: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM agent_versions WHERE node_id = ? AND version = ?",
            (node_id, version)).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["config"] = json.loads(item["config"])
    except ValueError:
        item["config"] = {}
    return item


def set_run_versions(run_id: str, versions: dict[str, int]) -> None:
    with connect() as con:
        con.execute(
            "UPDATE runs SET agent_versions_used = ? WHERE run_id = ?",
            (json.dumps(versions), run_id))


# --- reusable skills (Phase 5) ----------------------------------------------


def list_skills() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM skills ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_skill_by_name(name: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM skills WHERE name = ?",
                          (name,)).fetchone()
    return dict(row) if row else None


def add_skill(name: str, description: str, body: str) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO skills (name, description, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name.strip(), description.strip(), body, time.time()),
        )
        return int(cur.lastrowid)


def delete_skill(name: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM skills WHERE name = ?", (name,))


# --- schedules (Phase 5) ----------------------------------------------------


def list_schedules() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM schedules ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_schedule(schedule_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM schedules WHERE id = ?",
                          (schedule_id,)).fetchone()
    return dict(row) if row else None


def add_schedule(session_id: int, task: str, cron: str,
                 tz_offset: float = 0.0, enabled: bool = True) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO schedules (session_id, task, cron, tz_offset, "
            "enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, task, cron, float(tz_offset), int(bool(enabled)),
             time.time()),
        )
        return int(cur.lastrowid)


def set_schedule_enabled(schedule_id: int, enabled: bool) -> None:
    with connect() as con:
        con.execute("UPDATE schedules SET enabled = ? WHERE id = ?",
                    (int(bool(enabled)), schedule_id))


def delete_schedule(schedule_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def mark_schedule_run(schedule_id: int, run_id: str) -> None:
    """Record that a schedule fired now (tracks the repeat-guard window)."""
    with connect() as con:
        con.execute(
            "UPDATE schedules SET last_run_at = ?, last_run_id = ? WHERE id = ?",
            (time.time(), run_id, schedule_id),
        )


# --- Phase 6: roles and role permissions ------------------------------------


def list_roles(con: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    if con is None:
        with connect() as c:
            return list_roles(c)
    rows = con.execute("SELECT * FROM roles ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_role(name: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM roles WHERE name = ?",
                          (name,)).fetchone()
    return dict(row) if row else None


def add_role(name: str, description: str = "") -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO roles (name, description, created_at) "
            "VALUES (?, ?, ?)", (name.strip(), description.strip(), time.time()))
        return int(cur.lastrowid)


def delete_role(role_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        con.execute("DELETE FROM role_permissions WHERE role_id = ?",
                    (role_id,))


def list_role_permissions(con: sqlite3.Connection | None = None) -> list[dict]:
    if con is None:
        with connect() as c:
            return list_role_permissions(c)
    rows = con.execute(
        "SELECT p.id, r.name AS role_name, p.role_id, p.resource, p.access, "
        "p.approve_limit_amount FROM role_permissions p "
        "JOIN roles r ON r.id = p.role_id ORDER BY r.name, p.resource"
    ).fetchall()
    return [dict(r) for r in rows]


def role_permission_map() -> dict[str, dict[str, tuple[str, float | None]]]:
    """role name -> {resource: (access, approve_limit_amount)} (Phase 6)."""
    out: dict[str, dict[str, tuple[str, float | None]]] = {}
    for row in list_role_permissions():
        out.setdefault(str(row["role_name"]), {})[str(row["resource"])] = (
            str(row["access"]),
            row["approve_limit_amount"],
        )
    return out


def set_role_permission(role_id: int, resource: str, access: str,
                        limit: float | None = None) -> None:
    with connect() as con:
        cur = con.execute(
            "SELECT id FROM role_permissions WHERE role_id = ? AND resource = ?",
            (role_id, resource))
        existing = cur.fetchone()
        if existing:
            con.execute(
                "UPDATE role_permissions SET access = ?, approve_limit_amount = ? "
                "WHERE id = ?", (access, limit, existing["id"]))
        else:
            con.execute(
                "INSERT INTO role_permissions (role_id, resource, access, "
                "approve_limit_amount) VALUES (?, ?, ?, ?)",
                (role_id, resource, access, limit))


# --- Phase 6 section 2: API keys for the open HTTP interface ----------------

def _hash_key(plain: str) -> str:
    import hashlib

    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def create_api_key(label: str, role_id: int) -> tuple[int, str]:
    """Mint a key; only its SHA-256 hash is stored. Returns (id, plain key).

    The plain key is shown to the caller exactly once (API keys page).
    """
    import secrets

    plain = "fin_" + secrets.token_urlsafe(24)
    with connect() as con:
        cur = con.execute(
            "INSERT INTO api_keys (label, key_hash, role_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (label.strip(), _hash_key(plain), int(role_id), time.time()),
        )
        return int(cur.lastrowid), plain


def list_api_keys() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            "SELECT k.id, k.label, k.created_at, k.revoked, "
            "k.role_id, r.name AS role_name FROM api_keys k "
            "JOIN roles r ON r.id = k.role_id ORDER BY k.id"
        ).fetchall()
    return [dict(r) for r in rows]


def revoke_api_key(key_id: int) -> None:
    with connect() as con:
        con.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (int(key_id),))


def api_key_role(plain: str) -> str | None:
    """Role name for a valid (present, unrevoked) key; None otherwise."""
    if not plain:
        return None
    with connect() as con:
        row = con.execute(
            "SELECT k.revoked, r.name AS role_name FROM api_keys k "
            "JOIN roles r ON r.id = k.role_id WHERE k.key_hash = ?",
            (_hash_key(plain),),
        ).fetchone()
    if row is None or row["revoked"]:
        return None
    return str(row["role_name"])


# --- Phase 6 section 5: event triggers --------------------------------------

def _trigger_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    try:
        data["config"] = json.loads(data.get("config") or "{}")
    except ValueError:
        data["config"] = {}
    return data


def list_triggers() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM triggers ORDER BY id").fetchall()
    return [_trigger_row(r) for r in rows]


def get_trigger(trigger_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM triggers WHERE id = ?",
                          (int(trigger_id),)).fetchone()
    return _trigger_row(row) if row else None


def add_trigger(session_id: int, type: str, config: dict[str, Any],
                 task_template: str, secret: str = "",
                 enabled: bool = True) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO triggers (session_id, type, config, task_template, "
            "enabled, secret, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(session_id), type, json.dumps(config), task_template,
             int(bool(enabled)), secret, time.time()),
        )
        return int(cur.lastrowid)


def set_trigger_enabled(trigger_id: int, enabled: bool) -> None:
    with connect() as con:
        con.execute("UPDATE triggers SET enabled = ? WHERE id = ?",
                    (int(bool(enabled)), int(trigger_id)))


def delete_trigger(trigger_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM triggers WHERE id = ?", (int(trigger_id),))


def mark_trigger_fired(trigger_id: int, run_id: str,
                       file_path: str | None = None) -> None:
    with connect() as con:
        con.execute(
            "UPDATE triggers SET last_fired_at = ?, last_run_id = ?, "
            "last_file_path = COALESCE(?, last_file_path) WHERE id = ?",
            (time.time(), run_id, file_path, int(trigger_id)))


def clear_trigger_file(trigger_id: int) -> None:
    """The last fired file has been settled (processed/ or failed/)."""
    with connect() as con:
        con.execute("UPDATE triggers SET last_file_path = NULL WHERE id = ?",
                    (int(trigger_id),))
