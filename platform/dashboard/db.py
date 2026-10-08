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


def record_run(run_id: str, session_id: int, task: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, session_id, task, status, "
            "error, created_at, finished_at, state) VALUES (?, ?, ?, ?, ?, ?, "
            "?, ?)",
            (run_id, session_id, task, "running", None, time.time(), None,
             "queued"),
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
