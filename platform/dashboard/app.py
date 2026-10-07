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
from ai_operator.tracing import RUNS_ROOT
from platform.dashboard import db, flowcfg, html, runner
from platform.flow.models import Flow, load_flow
from platform.flow.validator import validate

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Comp Ops dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    flipped = runner.reconcile()
    if flipped:
        print(f"[dashboard] interrupted (orphaned) runs: {flipped}")


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
        state = {"running": "running", "waiting_input": "waiting_approval",
                 "cancelled": "interrupted", "failed": "failed"}.get(
                     handle.status) or str(row.get("state") or "")
        if handle.status == "done":
            state = str(row.get("state") or "completed")
    else:
        status = str(row.get("status") or "unknown")
        waiting = False
        question = ""
        error = row.get("error")
        state = str(row.get("state") or "")
    # An orphaned waiting_approval run (dashboard restarted mid-approval)
    # shows the resume form instead of the live answer form.
    orphaned_waiting = (state == "waiting_approval"
                        and (handle is None or handle.terminal))
    resume_question = ""
    if orphaned_waiting:
        for ev in reversed(_trace_events(run_id)):
            if ev.get("event") in ("approval_requested", "human_input_requested"):
                resume_question = str(ev.get("question", ""))
                break
    session = db.get_session(int(row["session_id"])) if row.get("session_id") else None
    run_active = handle is not None and not handle.terminal
    flow = load_flow(DEFAULT_FLOW_PATH)
    agents_html = html.agents_fragment(
        _agent_states(run_id, flow, run_active), run_active)
    return html.run_detail_page(
        run_id=run_id, task=str(row.get("task") or ""), status=status,
        session=session, waiting=waiting, question=question,
        report=_report(run_id), error=error, agents_html=agents_html,
        run_active=run_active, state=state, resume=orphaned_waiting,
        resume_question=resume_question,
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
def history_page() -> str:
    return html.history_page(db.list_runs(limit=200))


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


# --- flow editor -----------------------------------------------------------


@app.get("/flow", response_class=HTMLResponse, include_in_schema=False)
def flow_page() -> str:
    return html.flow_page(flowcfg.load_cfg())


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


# --- agents editor ---------------------------------------------------------


@app.get("/agents", response_class=HTMLResponse, include_in_schema=False)
def agents_page(message: str = "") -> str:
    return html.agents_page(flowcfg.load_cfg(), message=message)


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
def messages_page(message: str = "") -> str:
    return html.messages_page(flowcfg.load_cfg(), message=message)


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
def documents_page(tenant: str = "", error: str = "") -> str:
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
                               attachments, error=error)


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
