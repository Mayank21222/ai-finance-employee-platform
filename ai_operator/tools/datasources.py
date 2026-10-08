"""Phase 6 section 9: read-only data sources (CSV / SQLite file queries).

A company defines a data source (a CSV or SQLite file) and **saved queries**
against it on the connectors page. Each saved query is registered as one
read-level tool, so it flows through the exact same role checks as every
other tool (``roles.tool_gate`` needs at least ``read``).

No arbitrary SQL from the agent: the SQL text lives only in the
``saved_queries`` table and never comes from a tool call. The tool's args
model forbids extra arguments, so a call like ``{sql: "DROP ..."}`` is
rejected as INVALID_ARGUMENTS before any handler runs. Parameters come from
``{{vars.*}}` / ``{{session.*}}`` placeholders in the saved SQL: each
placeholder is rewritten to ``?`` and **bound as a value**, so a variable
full of quotes cannot break out of the query.

Sources are opened read-only (SQLite via ``mode=ro``; CSV loads into an
in-memory database), so even a misbehaving saved query cannot write.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.registry import ToolSpec, register, unregister

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_ROWS = 200
MAX_CELL = 200

_TPL_RE = re.compile(r"\{\{\s*(vars|session)\.(\w+)\s*\}\}")
_names: set[str] = set()

PLACEHOLDER = "(empty)"


class QueryArgs(BaseModel):
    """Saved queries take NO arguments: parameters come from session vars."""

    model_config = ConfigDict(extra="forbid")


def tool_names() -> set[str]:
    """Query tool names currently registered (for the validator)."""
    return set(_names)


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _render(sql: str) -> tuple[str, list[str]]:
    """Replace placeholders with ``?`` and collect the bound values."""
    variables, session = _get_runtime()
    values: list[str] = []

    def repl(m: "re.Match[str]") -> str:
        source = variables if m.group(1) == "vars" else session
        values.append(str(source.get(m.group(2), "")))
        return "?"

    return _TPL_RE.sub(repl, sql), values


def _get_runtime() -> tuple[dict[str, Any], dict[str, Any]]:
    # Late import: the connectors module owns the per-step snapshot the
    # execute node sets before every tool call.
    from ai_operator.tools import connectors

    return connectors.get_runtime()


def _open(source: dict[str, Any]) -> sqlite3.Connection:
    """Open the source read-only (CSV is loaded into an in-memory DB)."""
    stype = str(source.get("type") or "").lower()
    path = _resolve(str(source.get("path") or ""))
    if stype == "sqlite":
        if not path.is_file():
            raise FileNotFoundError(f"data source file not found: {path}")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return con
    if stype == "csv":
        if not path.is_file():
            raise FileNotFoundError(f"data source file not found: {path}")
        con = sqlite3.connect(":memory:")
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            return con
        header = [h.strip() or f"col{i + 1}"
                  for i, h in enumerate(rows[0])]
        # SQLite column names must be unique and non-empty
        seen: dict[str, int] = {}
        cols = []
        for h in header:
            seen[h] = seen.get(h, 0) + 1
            cols.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
        con.execute(
            "CREATE TABLE data (" +
            ", ".join(f'"{c}" TEXT' for c in cols) + ")")
        con.executemany(
            "INSERT INTO data VALUES (" +
            ", ".join("?" for _ in cols) + ")",
            [row[:len(cols)] + [""] * (len(cols) - len(row))
             for row in rows[1:]])
        return con
    raise ValueError(f"unknown data source type {stype!r} "
                     "(expected csv or sqlite)")


def _handler(sql: str, source: dict[str, Any],
             query_name: str) -> Callable[[Any], str]:
    def handle(_args: QueryArgs) -> str:
        statement, values = _render(sql)
        con = _open(source)
        try:
            cur = con.execute(statement, values)
            if cur.description is None:
                return f"{query_name}: statement executed (no rows)"
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS)
        finally:
            con.close()
        if not rows:
            return f"{query_name}: no rows matched."
        lines = ["\t".join(columns)]
        for row in rows:
            lines.append("\t".join(
                str(v)[:MAX_CELL] if v is not None else ""
                for v in row))
        more = " (row limit reached)" if len(rows) == MAX_ROWS else ""
        return "\n".join(lines) + more

    return handle


def sync(queries: list[dict[str, Any]]) -> list[str]:
    """Register exactly the saved queries as read tools; return errors.

    Mirrors ``connectors.sync``: previously registered query tools are
    removed first so deleted queries disappear from the registry.
    """
    errors: list[str] = []
    for name in list(_names):
        unregister(name)
    _names.clear()
    for query in queries:
        name = str(query.get("name") or "").strip()
        source = query.get("source") or {}
        try:
            register(ToolSpec(
                name=name,
                description=(f"Run the saved query '{name}' against the "
                             f"'{source.get('name', 'data')}' data source "
                             "(read-only; parameters come from session "
                             "variables)"),
                level=PermissionLevel.read,
                args_model=QueryArgs,
                handler=_handler(str(query.get("sql") or ""), source, name),
                timeout_seconds=15.0,
            ))
            _names.add(name)
        except Exception as exc:  # noqa: BLE001 - one bad query, not a crash
            errors.append(f"saved query '{name}': {exc}")
    return errors
