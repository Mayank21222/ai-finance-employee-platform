"""Minimal MCP (Model Context Protocol) server over stdio (Phase 6 section 2).

Exposes four tools - run_task, get_run_status, list_pending_approvals,
answer_approval - and implements them by calling the dashboard's versioned
HTTP API (/api/v1/...), so every call runs under an API key and passes the
exact same role checks as any other external agent.

Transport: newline-delimited JSON-RPC 2.0 on stdin/stdout (the simplest MCP
transport this stack supports with no new dependencies).

Run it with:

    FIN_API_KEY=fin_xxx FIN_DASHBOARD_URL=http://127.0.0.1:8001 \
        python -m platform.mcp_server

Tested with a scripted MCP client (JSON-RPC over stdio); see
tests/test_phase6_api.py. It has NOT been exercised against a third-party
MCP client such as Claude Desktop - see KNOWN_LIMITATIONS.md.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "fin-mcp", "version": "1.0"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_task",
        "description": (
            "Start a run of the finance employee: give a session id and a "
            "plain-English business request. Returns the run_id; poll "
            "get_run_status for progress."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "integer",
                               "description": "dashboard session id"},
                "task": {"type": "string", "description": "business request"},
            },
            "required": ["session_id", "task"],
        },
    },
    {
        "name": "get_run_status",
        "description": ("State, status, report, variables and token totals "
                        "of one run."),
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "list_pending_approvals",
        "description": "Approvals currently waiting for a human decision.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "answer_approval",
        "description": ("Answer a pending approval (approve/reject) or "
                        "clarification for a run. Approvals are still gated "
                        "by this API key's role and its approve limit."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "answer": {"type": "string",
                           "description": "approve | reject | a sentence"},
            },
            "required": ["run_id", "answer"],
        },
    },
]


def _base_url() -> str:
    return os.environ.get("FIN_DASHBOARD_URL",
                          "http://127.0.0.1:8001").rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("FIN_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def call_api(method: str, path: str, json_body: dict | None = None) -> tuple[int, Any]:
    """One HTTP call to the dashboard API. Returns (status_code, parsed body)."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.request(method, f"{_base_url()}{path}",
                              headers=_headers(), json=json_body)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"raw": resp.text[:500]}


def dispatch_tool(name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Run one MCP tool through the HTTP API. Returns (ok, text)."""
    try:
        if name == "run_task":
            status, body = call_api(
                "POST", "/api/v1/runs",
                {"session_id": int(args.get("session_id", 0)),
                 "task": str(args.get("task", ""))})
        elif name == "get_run_status":
            status, body = call_api(
                "GET", f"/api/v1/runs/{args.get('run_id', '')}")
        elif name == "list_pending_approvals":
            status, body = call_api("GET", "/api/v1/approvals?state=pending")
        elif name == "answer_approval":
            status, body = call_api(
                "POST", f"/api/v1/runs/{args.get('run_id', '')}/answer",
                {"answer": str(args.get("answer", ""))})
        else:
            return False, f"unknown tool: {name}"
    except httpx.HTTPError as exc:
        return False, f"dashboard API unreachable: {exc}"
    ok = 200 <= status < 300
    return ok, json.dumps(body, indent=2, default=str)


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; None means send no reply (notification)."""
    method = str(msg.get("method") or "")
    msg_id = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        args = dict(params.get("arguments") or {})
        if name not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32602, "message": f"unknown tool: {name}"}}
        ok, text = dispatch_tool(name, args)
        return {"jsonrpc": "2.0", "id": msg_id, "result": {
            "content": [{"type": "text", "text": text}], "isError": not ok}}
    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "error": {
        "code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle_message(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
