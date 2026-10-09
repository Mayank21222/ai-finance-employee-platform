"""SQLite persistence for employees and runs. One file, stdlib only."""

from __future__ import annotations

import json
import os
import sqlite3

from ai_operator.models import Employee, RunRecord


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS employees(id TEXT PRIMARY KEY, data TEXT)")
            c.execute("""CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, employee_id TEXT, employee_name TEXT, task TEXT,
                status TEXT, started REAL, duration REAL, cost REAL, trace TEXT, report TEXT)""")

    # --- employees ---------------------------------------------------- #
    def save_employee(self, emp: Employee) -> Employee:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO employees(id, data) VALUES(?, ?)", (emp.id, emp.model_dump_json()))
        return emp

    def get_employee(self, emp_id: str) -> Employee | None:
        row = self._conn().execute("SELECT data FROM employees WHERE id=?", (emp_id,)).fetchone()
        return Employee(**json.loads(row["data"])) if row else None

    def list_employees(self) -> list[Employee]:
        rows = self._conn().execute("SELECT data FROM employees ORDER BY id").fetchall()
        return [Employee(**json.loads(r["data"])) for r in rows]

    def delete_employee(self, emp_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM employees WHERE id=?", (emp_id,))

    # --- runs --------------------------------------------------------- #
    def save_run(self, run: RunRecord) -> RunRecord:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (run.id, run.employee_id, run.employee_name, run.task, run.status,
                       run.started, run.duration, run.cost, run.trace,
                       run.report.model_dump_json() if run.report else None))
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._conn().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        return RunRecord(
            id=row["id"], employee_id=row["employee_id"], employee_name=row["employee_name"],
            task=row["task"], status=row["status"], started=row["started"],
            duration=row["duration"], cost=row["cost"], trace=row["trace"],
            report=json.loads(row["report"]) if row["report"] else None,
        )

    def list_runs(self, limit: int = 100) -> list[RunRecord]:
        rows = self._conn().execute("SELECT * FROM runs ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
        return [self.get_run(r["id"]) for r in rows]