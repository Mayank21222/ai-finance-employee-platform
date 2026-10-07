"""SQLite storage for dashboard sessions and run history.

Path override via DASH_DB env var so tests never touch the real file.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = _REPO_ROOT / "dashboard.db"


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
            """
        )
        # Phase-3 state machine column (queued|running|waiting_approval|
        # completed|failed|interrupted); migrate pre-existing tables.
        try:
            con.execute("ALTER TABLE runs ADD COLUMN state TEXT "
                        "NOT NULL DEFAULT 'interrupted'")
        except sqlite3.OperationalError:
            pass
        row = con.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        if row["n"] == 0:
            con.execute(
                "INSERT INTO sessions (tenant, currency, approval_threshold, "
                "user_role, created_at) VALUES (?, ?, ?, ?, ?)",
                ("acme", "INR", 50000.0, "finance_operator", time.time()),
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
