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

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from ai_operator.graph import DEFAULT_FLOW_PATH
from ai_operator.permissions import PermissionLevel
from ai_operator.tools import connectors as connector_tools
from ai_operator.tracing import RUNS_ROOT
from platform.dashboard import db, flowcfg, html, runner
from platform.flow.models import Flow, load_flow
from platform.flow.validator import SUPPORTED_HTTP_METHODS, validate

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Comp Ops dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # Phase 4: the DB connector registry is loaded into the tool registry and
    # projected into the flow config so the next run registers those tools.
    connector_errors = flowcfg.publish_connectors()
    if connector_errors:
        print(f"[dashboard] connector registry: {'; '.join(connector_errors)}")
    flipped = runner.reconcile()
    if flipped:
        print(f"[dashboard] interrupted (orphaned) runs: {flipped}")


@app.on_event("shutdown")
def _shutdown() -> None:
    runner.shutdown()


def _mode(request: Request) -> str:
    """Phase 4: usage vs configuration mode, persisted in a cookie."""
    mode = str(request.cookies.get("app_mode", "use"))
    return mode if mode in ("use", "configure") else "use"


@app.get("/mode/{mode}", include_in_schema=False)
def mode_switch(mode: str, request: Request):
    """Toggle the dashboard between usage and configuration mode."""
    if mode not in ("use", "configure"):
        return RedirectResponse(
            request.headers.get("referer") or "/runs", status_code=303)
    response = RedirectResponse(
        request.headers.get("referer") or "/runs", status_code=303)
    response.set_cookie("app_mode", mode, path="/", max_age=60 * 60 * 24 * 365)
    return response


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
def runs_page(request: Request) -> str:
    return html.runs_page(db.list_runs(), db.list_sessions(),
                          mode=_mode(request))


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
def run_detail(run_id: str, request: Request):
    row = db.get_run(run_id)
    if row is None:
        return HTMLResponse(html.not_found(f"No run '{run_id}'."), status_code=404)
    handle = runner.get(run_id)
    events = _trace_events(run_id)
    if handle is not None:
        status = handle.status
        waiting = handle.status == "waiting_input"
        interrupt_kind = ""
        question = ""
        if handle.interrupt:
            interrupt_kind = str(handle.interrupt.get("kind", "clarification"))
            if interrupt_kind != "approval":
                question = str(handle.interrupt.get(
                    "question", handle.interrupt))
        error = handle.error
        state = {"running": "running", "waiting_input": "waiting_approval",
                 "cancelled": "interrupted", "failed": "failed"}.get(
                     handle.status) or str(row.get("state") or "")
        if handle.status == "done":
            state = str(row.get("state") or "completed")
    else:
        status = str(row.get("status") or "unknown")
        waiting = False
        interrupt_kind = ""
        question = ""
        error = row.get("error")
        state = str(row.get("state") or "")
    # An orphaned waiting_approval run (dashboard restarted mid-approval)
    # shows the resume form instead of the live answer form.
    orphaned_waiting = (state == "waiting_approval"
                        and (handle is None or handle.terminal))
    resume_question = ""
    if orphaned_waiting:
        for ev in reversed(events):
            if ev.get("event") in ("approval_requested", "human_input_requested"):
                resume_question = str(ev.get("question", ""))
                break
    session = db.get_session(int(row["session_id"])) if row.get("session_id") else None
    run_active = handle is not None and not handle.terminal
    # Phase 3: an approval interrupt renders as a decision card; only
    # clarifications keep the free-text answer form.
    approval_panel = html.approval_panel_html(
        run_id, session, events,
        visible=(waiting and interrupt_kind == "approval"))
    answer_waiting = waiting and interrupt_kind != "approval"
    flow = load_flow(DEFAULT_FLOW_PATH)
    agents_html = html.agents_fragment(
        _agent_states(run_id, flow, run_active), run_active)
    return html.run_detail_page(
        run_id=run_id, task=str(row.get("task") or ""), status=status,
        session=session, waiting=answer_waiting, question=question,
        report=_report(run_id), error=error, agents_html=agents_html,
        run_active=run_active, state=state, resume=orphaned_waiting,
        resume_question=resume_question, approval_panel=approval_panel,
        mode=_mode(request),
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
def answer_run(request: Request, run_id: str, answer: str = Form(...)):
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
    # HTMX (approval card buttons) re-renders the card in place so the
    # verdict + approver appear without a full round trip.
    if request.headers.get("HX-Request") == "true":
        row = db.get_run(run_id)
        session = (db.get_session(int(row["session_id"]))
                   if row and row.get("session_id") else None)
        return HTMLResponse(html.approval_card(
            run_id, session, _trace_events(run_id)))
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/runs/{run_id}/approval-card", response_class=HTMLResponse,
         include_in_schema=False)
def approval_card_fragment(run_id: str):
    """Poll target for the live approval card (pending or decided)."""
    row = db.get_run(run_id)
    if row is None:
        return HTMLResponse("", status_code=404)
    session = (db.get_session(int(row["session_id"]))
               if row.get("session_id") else None)
    return HTMLResponse(html.approval_card(run_id, session,
                                           _trace_events(run_id)))


@app.post("/runs/{run_id}/cancel", include_in_schema=False)
def cancel_run(run_id: str) -> Response:
    runner.cancel_run(run_id)
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.post("/runs/{run_id}/resume", include_in_schema=False)
def resume_run(run_id: str, answer: str = Form(...)):
    try:
        runner.resume_run(run_id, answer.strip())
    except RuntimeError as exc:
        return HTMLResponse(
            html.page("Run",
                      f'<h1 class="err">{html.esc(exc)}</h1>'
                      f'<p><a href="/runs/{html.esc(run_id)}">Back to run</a></p>',
                      active="runs"),
            status_code=409,
        )
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/history", response_class=HTMLResponse, include_in_schema=False)
def history_page(request: Request) -> str:
    return html.history_page(db.list_runs(limit=200), mode=_mode(request))


@app.get("/runs/{run_id}/trace", include_in_schema=False)
def run_trace(run_id: str):
    path = RUNS_ROOT / run_id / "trace.jsonl"
    if not path.is_file():
        return Response("no trace for this run\n", media_type="text/plain",
                        status_code=404)
    return Response(path.read_text(errors="replace"),
                    media_type="text/plain; charset=utf-8")


@app.get("/runs/{run_id}/evidence", include_in_schema=False)
def evidence_index(run_id: str):
    ev_dir = RUNS_ROOT / run_id / "evidence"
    names = sorted(p.name for p in ev_dir.glob("*") if p.is_file()) \
        if ev_dir.is_dir() else []
    return HTMLResponse(html.evidence_index_page(run_id, names))


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


# --- data models (Phase 4) -------------------------------------------------


def _field_audit() -> dict[str, list[dict]]:
    """Recent field-level writes per model, from the runs' trace files."""
    from ai_operator import datamodel

    by_model: dict[str, list[dict]] = {}
    field_of_model = {
        f.name: m.name for m in datamodel.load_models() for f in m.fields
    }
    if not field_of_model:
        return by_model
    latest_first = []
    for run_dir in sorted(RUNS_ROOT.glob("*/trace.jsonl"),
                          key=lambda p: p.stat().st_mtime, reverse=True):
        path = run_dir.parent / "trace.jsonl"
        try:
            for line in path.read_text(errors="replace").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("event") not in ("variable_written",
                                           "permission_denied"):
                    continue
                name = str(ev.get("name") or "")
                if name in field_of_model:
                    latest_first.append({
                        "model": field_of_model[name],
                        "run_id": ev.get("run_id", run_dir.parent.name),
                        "node": ev.get("node"), "name": name,
                        "old": ev.get("old"), "new": ev.get("new"),
                        "time": ev.get("time", ""), "denied": ev.get("event")
                        == "permission_denied",
                    })
        except (OSError, ValueError):
            continue
    for ev in latest_first:
        by_model.setdefault(ev["model"], []).append(ev)
    return by_model


@app.get("/models", response_class=HTMLResponse, include_in_schema=False)
def models_page(request: Request):
    from ai_operator import datamodel

    models = [m.model_dump() for m in datamodel.load_models()]
    return html.models_page(models, audit=_field_audit(),
                            mode=_mode(request))


@app.post("/models", include_in_schema=False)
def models_create(request: Request, name: str = Form(...),
                  description: str = Form("")):
    from ai_operator import datamodel

    name = name.strip()
    if not name or not all(c.isalnum() or c == "_" for c in name):
        return HTMLResponse(html.models_page(
            [m.model_dump() for m in datamodel.load_models()],
            error="Model name must be letters, digits, underscores.",
            mode=_mode(request)), status_code=400)
    if datamodel.get_model(name) is not None:
        return HTMLResponse(html.models_page(
            [m.model_dump() for m in datamodel.load_models()],
            error=f"Model '{name}' already exists.",
            mode=_mode(request)), status_code=400)
    models = datamodel.load_models()
    models.append(datamodel.DataModel(name=name, description=description.strip()))
    datamodel.save_models(models)
    return RedirectResponse("/models", status_code=303)


@app.post("/models/{name}/delete", include_in_schema=False)
def models_delete(request: Request, name: str):
    from ai_operator import datamodel

    models = [m for m in datamodel.load_models() if m.name != name]
    datamodel.save_models(models, seed_if_empty=False)
    return RedirectResponse("/models", status_code=303)


@app.post("/models/{name}/fields", include_in_schema=False)
def models_add_field(request: Request, name: str,
                     field_name: str = Form(...), field_type: str = Form(...),
                     required: str = Form(""),
                     description: str = Form(""),
                     write_agents: str = Form(""),
                     read_agents: str = Form("")):
    from ai_operator import datamodel

    models = datamodel.load_models()
    model = next((m for m in models if m.name == name), None)
    if model is None:
        return Response(status_code=404)
    field_name = field_name.strip()
    try:
        field = datamodel.FieldSpec(
            name=field_name, type=field_type,
            required=bool(required),
            description=description.strip(),
            write_agents=[a.strip() for a in write_agents.split(",")
                          if a.strip()],
            read_agents=[a.strip() for a in read_agents.split(",")
                         if a.strip()],
        )
        model.fields.append(field)
        datamodel.DataModel.model_validate(model.model_dump())  # uniqueness
        datamodel.save_models(models)
    except Exception as exc:
        return HTMLResponse(html.models_page(
            [m.model_dump() for m in models],
            error=str(exc), mode=_mode(request)), status_code=400)
    return RedirectResponse("/models", status_code=303)


@app.post("/models/{name}/fields/{field}/delete", include_in_schema=False)
def models_delete_field(request: Request, name: str, field: str):
    from ai_operator import datamodel

    models = datamodel.load_models()
    model = next((m for m in models if m.name == name), None)
    if model is None:
        return Response(status_code=404)
    model.fields = [f for f in model.fields if f.name != field]
    datamodel.save_models(models)
    return RedirectResponse("/models", status_code=303)


# --- connectors registry (Phase 4) ------------------------------------------


def _connectors_error(errors: list[str]) -> Response:
    return HTMLResponse(
        html.connectors_page(db.list_connectors(), error="; ".join(errors)),
        status_code=400,
    )


@app.get("/connectors", response_class=HTMLResponse, include_in_schema=False)
def connectors_page(request: Request) -> str:
    return html.connectors_page(db.list_connectors(), mode=_mode(request))


@app.post("/connectors", include_in_schema=False)
def connectors_create(name: str = Form(...), base_url: str = Form(...),
                      headers: str = Form("{}")):
    name = name.strip()
    errors: list[str] = []
    if not name or not all(c.isalnum() or c == "_" for c in name):
        errors.append("Connector name must be letters, digits or underscores.")
    if any(c["name"] == name for c in db.list_connectors()):
        errors.append(f"Connector '{name}' already exists.")
    try:
        parsed = json.loads(headers or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        errors.append(f"Default headers must be a JSON object ({exc}).")
    base_url = base_url.strip()
    if not base_url or " " in base_url:
        errors.append("Base URL is required and must not contain spaces.")
    if errors:
        return _connectors_error(errors)
    connector_id = db.add_connector(name, base_url, parsed)
    publish_errors = flowcfg.publish_connectors()
    if publish_errors:
        db.delete_connector(connector_id)  # rollback: never persist a
        return _connectors_error(publish_errors)  # connector that cannot publish
    return RedirectResponse("/connectors", status_code=303)


@app.post("/connectors/{connector_id}/delete", include_in_schema=False)
def connectors_delete(connector_id: int):
    if db.get_connector(connector_id) is None:
        return Response(status_code=404)
    db.delete_connector(connector_id)
    flowcfg.publish_connectors()
    return RedirectResponse("/connectors", status_code=303)


@app.post("/connectors/{connector_id}/endpoints", include_in_schema=False)
def connectors_add_endpoint(connector_id: int,
                            name: str = Form(...),
                            method: str = Form("GET"),
                            path: str = Form(...),
                            level: str = Form("read"),
                            description: str = Form(""),
                            response_fields: str = Form("")):
    if db.get_connector(connector_id) is None:
        return Response(status_code=404)
    name = name.strip()
    errors: list[str] = []
    if not name or not all(c.isalnum() or c == "_" for c in name):
        errors.append("Tool name must be letters, digits or underscores.")
    existing = {e.get("name") for c in db.list_connectors()
                for e in c.get("endpoints") or []}
    if name in existing:
        errors.append(f"A tool named '{name}' already exists.")
    from ai_operator.tools.registry import names as registry_names

    reserved = (set(registry_names()) - connector_tools.registered_names())
    if name in reserved:
        errors.append(f"'{name}' is a built-in tool name and is reserved.")
    if method.upper() not in SUPPORTED_HTTP_METHODS:
        errors.append(
            f"Unsupported method '{method}' "
            f"(supported: {', '.join(SUPPORTED_HTTP_METHODS)}).")
    if level not in PermissionLevel._value2member_map_:
        errors.append(f"Invalid permission level '{level}' "
                      "(read, reversible_write, irreversible_write).")
    path = path.strip()
    if not path or " " in path:
        errors.append("Path is required and must not contain spaces.")
    if errors:
        return _connectors_error(errors)
    endpoint = {
        "name": name, "method": method.upper(), "path": path,
        "level": level, "description": description.strip(),
        "response_fields": response_fields.strip(),
    }
    db.add_endpoint(connector_id, endpoint)
    publish_errors = flowcfg.publish_connectors()
    if publish_errors:
        db.delete_endpoint(connector_id, name)
        return _connectors_error(publish_errors)
    return RedirectResponse("/connectors", status_code=303)


@app.post("/connectors/{connector_id}/endpoints/{endpoint_name}/delete",
          include_in_schema=False)
def connectors_delete_endpoint(connector_id: int, endpoint_name: str):
    db.delete_endpoint(connector_id, endpoint_name)
    flowcfg.publish_connectors()
    return RedirectResponse("/connectors", status_code=303)


# --- audit trail (Phase 4) --------------------------------------------------


@app.get("/audit", response_class=HTMLResponse, include_in_schema=False)
def audit_page(request: Request, run_id: str = "", node: str = "",
               event: str = "") -> str:
    events = _audit_events(run_id=run_id, node=node, event=event)
    return html.audit_page(events, run_id=run_id, node=node, event=event,
                           mode=_mode(request))


def _audit_events(run_id: str = "", node: str = "", event: str = "",
                  limit: int = 500) -> list[dict]:
    """Read trace.jsonl from every run; no new data is stored for audit."""
    collected: list[dict] = []
    if not RUNS_ROOT.exists():
        return []
    for path in RUNS_ROOT.glob("*/trace.jsonl"):
        rid = path.parent.name
        if run_id and rid != run_id:
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if event and ev.get("event") != event:
                continue
            if node and ev.get("node") != node:
                continue
            ev.setdefault("run_id", rid)
            collected.append(ev)
    collected.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
    return collected[:limit]


# --- flow editor -----------------------------------------------------------


@app.get("/flow", response_class=HTMLResponse, include_in_schema=False)
def flow_page(request: Request) -> str:
    return html.flow_page(flowcfg.load_cfg(), mode=_mode(request))


@app.post("/flow/validate", response_class=HTMLResponse, include_in_schema=False)
def flow_validate() -> str:
    return html.validation_panel(validate(flowcfg.load_flow()))


@app.post("/flow/connections", include_in_schema=False)
def flow_add_connection(source: str = Form(...),
                        destination: str = Form(...)):
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if n.get("node_id") == source), None)
    if node is None or node.get("type") not in ("agent", "message"):
        return HTMLResponse(
            html.page("Flow editor",
                      '<h1 class="err">Only agent and message nodes accept '
                      'added connections.</h1><p><a href="/flow">Back</a></p>',
                      active="flow"),
            status_code=400,
        )
    connections = node.setdefault("next_node_ids", [])
    if destination not in connections:
        connections.append(destination)
    errors = flowcfg.save_cfg(cfg)
    if errors:
        return HTMLResponse(
            html.flow_page(cfg, errors=errors), status_code=400)
    return RedirectResponse("/flow", status_code=303)


@app.post("/flow/connections/remove", include_in_schema=False)
def flow_remove_connection(source: str = Form(...),
                           destination: str = Form(...)):
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if n.get("node_id") == source), None)
    if node is not None:
        connections = node.get("next_node_ids") or []
        if destination in connections:
            connections.remove(destination)
            errors = flowcfg.save_cfg(cfg)
            if errors:
                return HTMLResponse(
                    html.flow_page(cfg, errors=errors), status_code=400)
    return RedirectResponse("/flow", status_code=303)


@app.get("/flow/diagram", response_class=HTMLResponse, include_in_schema=False)
def flow_diagram() -> str:
    """The SVG fragment the diagram panel re-fetches after a node save."""
    return html.flow_svg(flowcfg.load_cfg())


@app.get("/flow/nodes/{node_id}", response_class=HTMLResponse,
         include_in_schema=False)
def flow_node_form(node_id: str) -> Response:
    cfg = flowcfg.load_cfg()
    found = any(str(n.get("node_id")) == node_id for n in cfg.get("nodes", []))
    return HTMLResponse(html.node_edit_panel(cfg, node_id),
                        status_code=200 if found else 404)


@app.post("/flow/nodes/{node_id}", response_class=HTMLResponse,
          include_in_schema=False)
def flow_node_save(node_id: str,
                   system_prompt: str = Form(""),
                   instructions: str = Form(""),
                   save_as: str = Form(""),
                   fallback_next: str = Form(""),
                   template: str = Form(""),
                   next_node_id: str = Form(""),
                   match_next: str = Form(""),
                   mismatch_next: str = Form("")) -> Response:
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if str(n.get("node_id")) == node_id), None)
    if node is None:
        return HTMLResponse(html.node_edit_panel(cfg, node_id),
                            status_code=404)
    old_config = json.loads(json.dumps(node))  # pre-change snapshot (Phase 5)
    kind = node.get("type")
    if kind == "agent":
        node["system_prompt"] = system_prompt
        node["instructions"] = instructions
        node["save_as"] = save_as
        node["fallback_next"] = fallback_next
    elif kind == "message":
        node["template"] = template
        if next_node_id:
            node["next_node_ids"] = [next_node_id]
        node["fallback_next"] = fallback_next
    elif kind == "verify":
        node["match_next"] = match_next
        node["mismatch_next"] = mismatch_next
    else:
        return HTMLResponse(html.node_edit_panel(
            cfg, node_id, message="End nodes have nothing to edit."))
    errors = flowcfg.save_cfg(cfg)  # validate first: bad edits never persist
    if errors:
        return HTMLResponse(html.node_edit_panel(cfg, node_id,
                                                 error=errors[0]),
                            status_code=400)
    if kind == "agent":
        # Phase 5: every panel save records the configuration it replaced.
        # Only successful saves create versions - a rejected edit changed
        # nothing, so there is no history entry to keep.
        db.add_version(node_id, old_config)
    response = HTMLResponse(html.node_edit_panel(cfg, node_id,
                                                 message="Saved."))
    response.headers["HX-Trigger"] = "refresh-diagram"
    return response


@app.get("/flow/nodes/{node_id}/versions", response_class=HTMLResponse,
         include_in_schema=False)
def flow_node_versions(node_id: str) -> Response:
    cfg = flowcfg.load_cfg()
    found = any(str(n.get("node_id")) == node_id for n in cfg.get("nodes", []))
    if not found:
        return HTMLResponse(html.node_edit_panel(cfg, node_id),
                            status_code=404)
    return HTMLResponse(html.node_versions_panel(node_id,
                                                 db.list_versions(node_id)))


@app.get("/flow/nodes/{node_id}/versions/{version}",
         response_class=HTMLResponse, include_in_schema=False)
def flow_node_version_view(node_id: str, version: int) -> Response:
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if str(n.get("node_id")) == node_id), None)
    if node is None:
        return HTMLResponse(html.node_edit_panel(cfg, node_id),
                            status_code=404)
    snapshot = db.get_version(node_id, version)
    if snapshot is None:
        return HTMLResponse(
            html.node_versions_panel(node_id, db.list_versions(node_id),
                                     error=f"No version v{version}."),
            status_code=404)
    return HTMLResponse(html.node_version_compare(node_id, node, snapshot))


@app.post("/flow/nodes/{node_id}/versions/{version}/restore",
          response_class=HTMLResponse, include_in_schema=False)
def flow_node_version_restore(node_id: str, version: int) -> Response:
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if str(n.get("node_id")) == node_id), None)
    if node is None:
        return HTMLResponse(html.node_edit_panel(cfg, node_id),
                            status_code=404)
    snapshot = db.get_version(node_id, version)
    if snapshot is None:
        return HTMLResponse(
            html.node_versions_panel(node_id, db.list_versions(node_id),
                                     error=f"No version v{version}."),
            status_code=404)
    old_config = json.loads(json.dumps(node))
    node.clear()
    node.update(snapshot["config"])
    errors = flowcfg.save_cfg(cfg)
    if errors:
        node.clear()
        node.update(old_config)  # nothing persisted; restore in-memory copy
        return HTMLResponse(html.node_edit_panel(cfg, node_id,
                                                 error=errors[0]),
                            status_code=400)
    # The restore itself lands in the history: the configuration it replaced
    # is snapshotted exactly like a normal save, so no state is ever lost.
    db.add_version(node_id, old_config, label=f"restore of v{version}")
    response = HTMLResponse(html.node_edit_panel(
        cfg, node_id, message=f"Restored v{version}."))
    response.headers["HX-Trigger"] = "refresh-diagram"
    return response


# --- agents editor ---------------------------------------------------------


@app.get("/agents", response_class=HTMLResponse, include_in_schema=False)
def agents_page(request: Request, message: str = "") -> str:
    return html.agents_page(flowcfg.load_cfg(), message=message,
                            mode=_mode(request))


@app.post("/agents", include_in_schema=False)
def agents_create(node_id: str = Form(...),
                  next_node_ids: str = Form(...),
                  fallback_next: str = Form("")):
    cfg = flowcfg.load_cfg()
    if any(n.get("node_id") == node_id for n in cfg.get("nodes", [])):
        return HTMLResponse(
            html.agents_page(
                cfg, message=f"Node id '{node_id}' already exists."),
            status_code=400,
        )
    cfg.setdefault("nodes", []).append({
        "type": "agent", "node_id": node_id, "system_prompt": "",
        "instructions": "", "documents": [], "tools_enabled": [],
        "next_node_ids": [next_node_ids],
        "fallback_next": fallback_next or None, "save_as": None,
    })
    errors = flowcfg.save_cfg(cfg)
    if errors:
        return HTMLResponse(html.flow_page(cfg, errors=errors),
                            status_code=400)
    return RedirectResponse("/agents", status_code=303)


@app.post("/agents/{node_id}", include_in_schema=False)
def agents_edit(node_id: str, system_prompt: str = Form(""),
                instructions: str = Form(""), save_as: str = Form(""),
                fallback_next: str = Form("")):
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if n.get("node_id") == node_id and n.get("type") == "agent"),
                None)
    if node is None:
        return Response(status_code=404)
    node["system_prompt"] = system_prompt
    node["instructions"] = instructions
    node["save_as"] = save_as.strip() or None
    node["fallback_next"] = fallback_next or None
    errors = flowcfg.save_cfg(cfg)
    if errors:
        return HTMLResponse(html.agents_page(cfg, message=errors[0]),
                            status_code=400)
    return RedirectResponse("/agents", status_code=303)


# --- message nodes ---------------------------------------------------------


@app.get("/messages", response_class=HTMLResponse, include_in_schema=False)
def messages_page(request: Request, message: str = "") -> str:
    return html.messages_page(flowcfg.load_cfg(), message=message,
                              mode=_mode(request))


@app.post("/messages", include_in_schema=False)
def messages_create(node_id: str = Form(...), template: str = Form(...),
                    next_node_id: str = Form(...),
                    fallback_next: str = Form("")):
    cfg = flowcfg.load_cfg()
    if any(n.get("node_id") == node_id for n in cfg.get("nodes", [])):
        return HTMLResponse(
            html.messages_page(
                cfg, message=f"Node id '{node_id}' already exists."),
            status_code=400,
        )
    cfg.setdefault("nodes", []).append({
        "type": "message", "node_id": node_id, "template": template,
        "next_node_ids": [next_node_id],
        "fallback_next": fallback_next or None,
    })
    errors = flowcfg.save_cfg(cfg)
    if errors:
        return HTMLResponse(html.messages_page(cfg, message=errors[0]),
                            status_code=400)
    return RedirectResponse("/messages", status_code=303)


@app.post("/messages/{node_id}", include_in_schema=False)
def messages_edit(node_id: str, template: str = Form(...),
                  next_node_id: str = Form(...),
                  fallback_next: str = Form("")):
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if n.get("node_id") == node_id
                 and n.get("type") == "message"), None)
    if node is None:
        return Response(status_code=404)
    node["template"] = template
    node["next_node_ids"] = [next_node_id]
    node["fallback_next"] = fallback_next or None
    errors = flowcfg.save_cfg(cfg)
    if errors:
        return HTMLResponse(html.messages_page(cfg, message=errors[0]),
                            status_code=400)
    return RedirectResponse("/messages", status_code=303)


# --- documents -------------------------------------------------------------


def _company_dir(tenant: str) -> Path:
    return Path(__file__).resolve().parents[2] / "company_data" / tenant


def _safe_filename(name: str) -> str:
    cleaned = Path(name).name
    cleaned = "".join(c for c in cleaned if c.isalnum() or c in "._- ")
    return cleaned.strip() or "upload.txt"


@app.get("/documents", response_class=HTMLResponse, include_in_schema=False)
def documents_page(request: Request, tenant: str = "", error: str = "") -> str:
    sessions = db.list_sessions()
    tenants = sorted({s["tenant"] for s in sessions})
    tenant = tenant or (tenants[0] if tenants else "acme")
    target = _company_dir(tenant)
    files = sorted(str(p.relative_to(target))
                   for p in target.rglob("*") if p.is_file()
                   ) if target.is_dir() else []
    cfg = flowcfg.load_cfg()
    agents = [n["node_id"] for n in cfg.get("nodes", [])
              if n.get("type") == "agent"]
    attachments = {n["node_id"]: list(n.get("documents") or [])
                   for n in cfg.get("nodes", []) if n.get("type") == "agent"}
    return html.documents_page(tenant, tenants, files, agents,
                               attachments, error=error,
                               mode=_mode(request))


@app.post("/documents/upload", include_in_schema=False)
async def documents_upload(tenant: str = Form(...),
                           file: UploadFile = File(...)):
    name = _safe_filename(file.filename or "upload.txt")
    if Path(name).suffix.lower() not in (".txt", ".md", ".csv", ".json",
                                         ".pdf"):
        from urllib.parse import quote

        return RedirectResponse(
            "/documents?error=" + quote("Only txt, md, csv, json, pdf files."),
            status_code=303)
    target = _company_dir(tenant)
    target.mkdir(parents=True, exist_ok=True)
    body = await file.read()
    if len(body) > 2_000_000:
        from urllib.parse import quote

        return RedirectResponse(
            "/documents?error=" + quote("File larger than 2MB."),
            status_code=303)
    (target / name).write_bytes(body)
    if Path(name).suffix.lower() == ".pdf":
        # Phase 3: extract text at upload time into a .txt sibling so the
        # agent can actually read the document (KNOWN_LIMITATIONS item 12).
        from urllib.parse import quote

        from platform.dashboard.pdftext import extract_pdf_text

        txt_path = (target / name).with_suffix(".txt")
        try:
            txt_path.write_text(extract_pdf_text(body), encoding="utf-8")
        except Exception as exc:
            txt_path.write_text("", encoding="utf-8")
            warning = (f"PDF text extraction failed for {name}: "
                       f"{type(exc).__name__}: {exc} (stored empty .txt)")
            print(f"[documents] WARNING {warning}")
            return RedirectResponse(
                "/documents?error=" + quote(warning),
                status_code=303)
    return RedirectResponse(f"/documents?tenant={tenant}", status_code=303)


@app.post("/documents/attach", include_in_schema=False)
def documents_attach(tenant: str = Form(...), filename: str = Form(...),
                     agents: list[str] = Form(default=[])):
    rel = f"company_data/{tenant}/{filename}"
    cfg = flowcfg.load_cfg()
    for node in cfg.get("nodes", []):
        if node.get("type") != "agent":
            continue
        docs = node.setdefault("documents", [])
        should = node["node_id"] in agents
        if should and rel not in docs:
            docs.append(rel)
        elif not should and rel in docs:
            docs.remove(rel)
    errors = flowcfg.save_cfg(cfg)
    if errors:
        from urllib.parse import quote

        return RedirectResponse("/documents?error=" + quote(errors[0][:200]),
                                status_code=303)
    return RedirectResponse(f"/documents?tenant={tenant}", status_code=303)
