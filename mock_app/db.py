"""SQLite persistence for the mock payables app."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "payables.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor TEXT NOT NULL,
    amount REAL NOT NULL,
    due_date TEXT NOT NULL,
    source TEXT DEFAULT 'form',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def add_invoice(vendor: str, amount: float, due_date: str, source: str = "form") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO invoices (vendor, amount, due_date, source) VALUES (?, ?, ?, ?)",
            (vendor, amount, due_date, source),
        )
        return int(cur.lastrowid)


def list_invoices(vendor: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if vendor:
            rows = conn.execute(
                "SELECT * FROM invoices WHERE vendor = ? ORDER BY id", (vendor,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM invoices ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def reset() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM invoices")
