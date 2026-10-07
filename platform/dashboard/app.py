"""Dashboard FastAPI app.

Run it with:
    uvicorn platform.dashboard.app:app --port 8001
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (FileResponse, HTMLResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from ai_operator.graph import DEFAULT_FLOW_PATH
from ai_operator.tracing import RUNS_ROOT
from platform.dashboard import db, html, runner
from platform.flow.models import Flow, load_flow
from platform.flow.validator import validate

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Comp Ops dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.on_event("shutdown")
def _shutdown() -> None:
    runner.shutdown()


def _trace_events(run_id: str) -> list[dict]:
    path = RUNS_ROOT / run_id / "trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _report(run_id: str) -> dict | None:
    path = RUNS_ROOT / run_id / "report.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _agent_states(run_id: str, flow: Flow, run_active: bool) -> list[dict]:
    from ai_operator.tools import registry

    agents = [n for n in flow.nodes if n.type == "agent"]
    entered: set[str] = set()
    saved: dict[str, tuple[str, object]] = {}
    current: str | None = None
    last_decision: dict | None = None
    for ev in _trace_events(run_id):
        kind = ev.get("event")
        if kind == "node_entered" and ev.get("node_type") == "agent":
            entered.add(str(ev.get("node")))
            current = str(ev.get("node"))
        elif kind == "variable_written":
            saved[str(ev.get("name"))] = (str(ev.get("node")), ev.get("new"))
        elif kind == "decision":
            last_decision = ev
    out = []
    for node in agents:
        var = getattr(node, "save_as", None)
        saved_hit = saved.get(var) if var else None
        has_save = bool(saved_hit) and saved_hit[0] == node.node_id
        answer = f"{var} = {saved_hit[1]}" if has_save else ""
        if has_save:
            status = "complete"
        elif node.node_id not in entered:
            status = "idle"
        elif run_active:
            status = "running"
        else:
            status = "failed"
        reasoning = ""
        if current == node.node_id and last_decision is not None:
            reasoning = str(last_decision.get("thought") or "")
        out.append({
            "node_id": node.node_id,
            "save_as": var,
            "status": status,
            "reasoning": reasoning,
            "answer": answer,
            "tools": list(getattr(node, "tools_enabled", None) or []),
            "all_tools": registry.names(),
        })
    return out


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/runs", status_code=303)


# --- run console -----------------------------------------------------------


@app.get("/runs", response_class=HTMLResponse, include_in_schema=False)
def runs_page() -> str:
    return html.runs_page(db.list_runs(), db.list_sessions())


@app.post("/runs", include_in_schema=False)
def start_run(session_id: int = Form(...), task: str = Form(...),
              failures: str = Form(""), fresh: str | None = Form(None)):
    try:
        handle = runner.start_run(task.strip(), session_id,
                                  failures=failures, fresh=bool(fresh))
    except RuntimeError as exc:
        return HTMLResponse(
            html.page("Run console",
                      f'<h1 class="err">{html.esc(exc)}</h1>'
                      '<p><a href="/runs">Back to the run console</a></p>',
                      active="runs"),
            status_code=409,
        )
    return RedirectResponse(f"/runs/{handle.run_id}", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse,
         include_in_schema=False)
def run_detail(run_id: str):
    row = db.get_run(run_id)
    if row is None:
        return HTMLResponse(html.not_found(f"No run '{run_id}'."), status_code=404)
    handle = runner.get(run_id)
    if handle is not None:
        status = handle.status
        waiting = handle.status == "waiting_input"
        question = ""
        if handle.interrupt:
            question = str(handle.interrupt.get("question", handle.interrupt))
        error = handle.error
    else:
        status = str(row.get("status") or "unknown")
        waiting = False
        question = ""
        error = row.get("error")
    session = db.get_session(int(row["session_id"])) if row.get("session_id") else None
    run_active = handle is not None and not handle.terminal
    flow = load_flow(DEFAULT_FLOW_PATH)
    agents_html = html.agents_fragment(
        _agent_states(run_id, flow, run_active), run_active)
    return html.run_detail_page(
        run_id=run_id, task=str(row.get("task") or ""), status=status,
        session=session, waiting=waiting, question=question,
        report=_report(run_id), error=error, agents_html=agents_html,
        run_active=run_active,
    )


@app.get("/runs/{run_id}/events", include_in_schema=False)
async def run_events(run_id: str):
    trace_path = RUNS_ROOT / run_id / "trace.jsonl"

    async def gen():
        offset = 0
        last_data = time.monotonic()
        while True:
            new = b""
            try:
                with trace_path.open("rb") as fh:
                    fh.seek(offset)
                    new = fh.read()
                    offset = fh.tell()
            except OSError:
                pass
            if new:
                for line in new.decode(errors="replace").splitlines():
                    if line.strip():
                        yield f"data: {line}\n\n"
                last_data = time.monotonic()
            handle = runner.get(run_id)
            finished = handle is None or handle.terminal
            if finished and not new and time.monotonic() - last_data > 0.5:
                status = handle.status if handle else "done"
                payload = json.dumps({"event": "__done__", "status": status})
                yield f"data: {payload}\n\n"
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/runs/{run_id}/answer", include_in_schema=False)
def answer_run(run_id: str, answer: str = Form(...)):
    try:
        runner.answer_run(run_id, answer.strip())
    except RuntimeError as exc:
        return HTMLResponse(
            html.page("Run",
                      f'<h1 class="err">{html.esc(exc)}</h1>'
                      f'<p><a href="/runs/{html.esc(run_id)}">Back to run</a></p>',
                      active="runs"),
            status_code=400,
        )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/cancel", include_in_schema=False)
def cancel_run(run_id: str) -> Response:
    runner.cancel_run(run_id)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}/agents", include_in_schema=False,
         response_class=HTMLResponse)
def agents_fragment(run_id: str):
    run_active = False
    handle = runner.get(run_id)
    if handle is not None:
        run_active = not handle.terminal
    return html.agents_fragment(
        _agent_states(run_id, load_flow(DEFAULT_FLOW_PATH), run_active),
        run_active,
    )


@app.get("/runs/{run_id}/evidence/{name}", include_in_schema=False)
def evidence(run_id: str, name: str):
    if Path(name).name != name:
        return Response(status_code=404)
    path = RUNS_ROOT / run_id / "evidence" / name
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path)


# --- agents: tool toggles --------------------------------------------------


@app.post("/agents/{node_id}/tools/{tool}", include_in_schema=False,
          response_class=HTMLResponse)
def toggle_tool(node_id: str, tool: str):
    from ai_operator.tools import registry

    if tool not in registry.names():
        return HTMLResponse(
            html.tool_badge(node_id, tool, False, note="unknown tool"),
            status_code=404,
        )
    cfg = json.loads(DEFAULT_FLOW_PATH.read_text())
    node = next((n for n in cfg.get("nodes", [])
                 if n.get("node_id") == node_id), None)
    if node is None or node.get("type") != "agent":
        return HTMLResponse(
            html.tool_badge(node_id, tool, False, note="no such agent"),
            status_code=404,
        )
    tools = node.setdefault("tools_enabled", [])
    original = list(tools)
    if tool in tools:
        tools.remove(tool)
    else:
        tools.append(tool)
    try:
        errors = validate(Flow.model_validate(cfg))
    except Exception as exc:  # malformed config we should not have shipped
        errors = [str(exc)]
    if errors:
        node["tools_enabled"] = original
        return html.tool_badge(node_id, tool, tool in original,
                               note=errors[0])
    DEFAULT_FLOW_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return html.tool_badge(node_id, tool, tool in tools)


# --- sessions --------------------------------------------------------------


@app.get("/sessions", response_class=HTMLResponse, include_in_schema=False)
def sessions_get(error: str = "") -> str:
    return html.sessions_page(db.list_sessions(), error=error)


@app.post("/sessions", include_in_schema=False)
def sessions_create(tenant: str = Form(...), currency: str = Form(...),
                    approval_threshold: str = Form(...),
                    user_role: str = Form(...)) -> Response:
    try:
        threshold = float(approval_threshold)
    except ValueError:
        return HTMLResponse(
            html.sessions_page(db.list_sessions(),
                               error="Approval threshold must be a number."),
            status_code=400,
        )
    db.add_session(tenant.strip(), currency.strip(), threshold, user_role.strip())
    return RedirectResponse("/sessions", status_code=303)


@app.post("/sessions/{session_id}/delete", include_in_schema=False)
def sessions_delete(session_id: int) -> Response:
    db.delete_session(session_id)
    return RedirectResponse("/sessions", status_code=303)
