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

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse,
                               JSONResponse, RedirectResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

from ai_operator.graph import DEFAULT_FLOW_PATH
from ai_operator import roles
from ai_operator.llm import get_client
from ai_operator.permissions import PermissionLevel
from ai_operator.tools import connectors as connector_tools
from ai_operator.tracing import RUNS_ROOT
from platform.dashboard import (channels, db, flowcfg, html, runner,
                                scheduler)
from platform.dashboard import drafting
from platform.flow import compiler
from platform.flow.models import Flow, load_flow
from platform.flow.validator import SUPPORTED_HTTP_METHODS, validate

STATIC_DIR = Path(__file__).resolve().parent / "static"

_APPROVING_ANSWERS = {"y", "yes", "approve", "approved"}

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
    # Phase 5: start the background scheduler (checks every 60 seconds).
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()
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
    return StreamingResponse(_sse_gen(run_id),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                     "X-Accel-Buffering": "no"})


async def _sse_gen(run_id: str):
    """Tail trace.jsonl as SSE until the run is terminal (Phase 6: shared by
    the run console and the versioned API's events endpoint)."""
    trace_path = RUNS_ROOT / run_id / "trace.jsonl"
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


def _approval_gate(run_id: str, answer: str,
                   role: str) -> tuple[bool, str, float | None]:
    """May `role` submit this answer for this run? (Phase 6 section 1/2).

    Only approving answers are gated (by the role's approve limit on the
    live interrupt's amount); a refusal is traced as approval_denied_role
    and never reaches the run. Shared by the console /answer form (session
    role) and the API /api/v1/runs/{id}/answer (API key role).
    """
    if answer.strip().lower() not in _APPROVING_ANSWERS:
        return True, "", None
    handle = runner.get(run_id)
    payload = (handle.interrupt if handle is not None else None) or {}
    if not role or payload.get("kind") != "approval":
        return True, "", None
    amount = roles.approval_amount(payload.get("tool_args"))
    can_approve, reason = roles.approve_gate(role, amount)
    if not can_approve:
        from ai_operator.tracing import TraceLogger
        TraceLogger(run_id).event(
            "approval_denied_role", role=role, reason=reason,
            tool_name=payload.get("tool_name", ""), amount=amount,
        )
    return can_approve, reason, amount


@app.post("/runs/{run_id}/answer", include_in_schema=False)
def answer_run(request: Request, run_id: str, answer: str = Form(...)):
    answer = answer.strip()
    # Phase 6: the approver's role must hold `approve` on the action and stay
    # within its amount limit. A denied approval is rejected in code - it
    # never reaches the run - and is traced as approval_denied_role.
    row = db.get_run(run_id)
    session = (db.get_session(int(row["session_id"]))
               if row and row.get("session_id") else None)
    role = str((session or {}).get("user_role") or "")
    can_approve, reason, _ = _approval_gate(run_id, answer, role)
    if not can_approve:
        return HTMLResponse(
            html.page("Approval denied",
                      f'<h1 class="err">requires a higher role</h1>'
                      f'<p>{html.esc(role)} cannot approve this payment: '
                      f"{html.esc(reason)}.</p>"
                      f'<p><a href="/runs/{html.esc(run_id)}">'
                      f"Back to run</a></p>", active="runs"),
            status_code=403,
        )
    try:
        runner.answer_run(run_id, answer)
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
    rows = db.list_runs(limit=200)
    for row in rows:  # Phase 5: attach estimated token spend for display
        row["tokens"] = (_report(row["run_id"]) or {}).get("tokens")
    return html.history_page(rows, mode=_mode(request))


# --- schedules (Phase 5) ----------------------------------------------------


@app.get("/schedules", response_class=HTMLResponse, include_in_schema=False)
def schedules_page(request: Request) -> str:
    return html.schedules_page(db.list_schedules(), db.list_sessions(),
                               mode=_mode(request))


@app.post("/schedules", response_class=HTMLResponse, include_in_schema=False)
def schedules_add(session_id: int = Form(...), task: str = Form(...),
                  cron: str = Form(...), tz_offset: float = Form(0.0)):
    if scheduler.parse_cron(cron) is None:
        return html.schedules_page(
            db.list_schedules(), db.list_sessions(),
            error="cron must be 'daily HH:MM' or 'weekly mon HH:MM'.")
    db.add_schedule(session_id, task.strip(), cron.strip().lower(), tz_offset)
    return html.schedules_page(db.list_schedules(), db.list_sessions(),
                               message="Schedule added.")


@app.post("/schedules/{schedule_id}/toggle", include_in_schema=False)
def schedules_toggle(schedule_id: int):
    row = db.get_schedule(schedule_id)
    if row is not None:
        db.set_schedule_enabled(schedule_id, not bool(row["enabled"]))
    return RedirectResponse("/schedules", status_code=303)


@app.post("/schedules/{schedule_id}/delete", include_in_schema=False)
def schedules_delete(schedule_id: int):
    db.delete_schedule(schedule_id)
    return RedirectResponse("/schedules", status_code=303)


# --- triggers (Phase 6 section 5) -------------------------------------------

@app.get("/triggers", response_class=HTMLResponse, include_in_schema=False)
def triggers_page(request: Request, message: str = "", error: str = "") -> str:
    return html.triggers_page(db.list_triggers(), db.list_sessions(),
                              message=message, error=error,
                              mode=_mode(request))


@app.post("/triggers", include_in_schema=False)
def triggers_add(session_id: int = Form(...), type: str = Form(...),
                  path: str = Form(""), task_template: str = Form(...),
                  secret: str = Form("")):
    if type not in ("inbox_folder", "webhook"):
        return HTMLResponse(html.triggers_page(
            db.list_triggers(), db.list_sessions(),
            error="Type must be inbox_folder or webhook."), status_code=400)
    if db.get_session(session_id) is None:
        return HTMLResponse(html.triggers_page(
            db.list_triggers(), db.list_sessions(),
            error="Unknown session."), status_code=400)
    if not task_template.strip():
        return HTMLResponse(html.triggers_page(
            db.list_triggers(), db.list_sessions(),
            error="A task template is required."), status_code=400)
    if type == "webhook" and not secret.strip():
        import secrets as _secrets

        secret = _secrets.token_urlsafe(16)  # shown once on the page
    db.add_trigger(session_id, type, {"path": path.strip()},
                   task_template.strip(), secret=secret.strip())
    _audit_event("trigger_created", type=type)
    message = "Trigger added."
    if type == "webhook" and secret.strip():
        message = ("Trigger added. Webhook secret - shown only now; send "
                   "it as the X-Trigger-Secret header: " + secret.strip())
    return HTMLResponse(html.triggers_page(
        db.list_triggers(), db.list_sessions(), message=message))


@app.post("/triggers/{trigger_id}/toggle", include_in_schema=False)
def triggers_toggle(trigger_id: int):
    row = db.get_trigger(trigger_id)
    if row is not None:
        db.set_trigger_enabled(trigger_id, not bool(row["enabled"]))
    return RedirectResponse("/triggers", status_code=303)


@app.post("/triggers/{trigger_id}/delete", include_in_schema=False)
def triggers_delete(trigger_id: int):
    db.delete_trigger(trigger_id)
    _audit_event("trigger_deleted", trigger_id=trigger_id)
    return RedirectResponse("/triggers", status_code=303)


@app.post("/api/v1/triggers/{trigger_id}/fire", include_in_schema=False)
def api_fire_trigger(trigger_id: int, request: Request,
                      payload: dict = Body(default={})):
    """Webhook entry point: authenticated by the trigger's own secret."""
    from platform.dashboard import triggers

    status, body = triggers.fire_webhook(
        trigger_id, payload or {},
        str(request.headers.get("x-trigger-secret") or ""))
    return JSONResponse(body, status_code=status)


# --- approval and alert channels (Phase 6 section 6) ------------------------

CHANNEL_TYPES = ("webhook", "email_smtp")


@app.get("/channels", response_class=HTMLResponse, include_in_schema=False)
def channels_page(request: Request, message: str = "", error: str = "") -> str:
    return html.channels_page(db.list_channels(), db.list_notifications(),
                              message=message, error=error,
                              mode=_mode(request))


@app.post("/channels", include_in_schema=False)
def channels_add(name: str = Form(...), type: str = Form(...),
                 config: str = Form("{}"),
                 enabled: str | None = Form(None)):
    def back(msg: str = "", err: str = "") -> HTMLResponse:
        return HTMLResponse(html.channels_page(
            db.list_channels(), db.list_notifications(),
            message=msg, error=err, mode="configure"))

    if type not in CHANNEL_TYPES:
        return back(err=f"Unknown channel type {type!r}; use "
                        f"{' or '.join(CHANNEL_TYPES)}.")
    try:
        cfg = json.loads(config or "{}")
    except ValueError:
        return back(err="Channel config must be a JSON object.")
    if not isinstance(cfg, dict):
        return back(err="Channel config must be a JSON object.")
    if type == "webhook" and not str(cfg.get("url") or "").strip():
        return back(err='A webhook channel needs a "url" in its config.')
    if type == "email_smtp" and not str(cfg.get("host") or "").strip():
        return back(err='An email channel needs a "host" in its config.')
    channel_id = db.add_channel(name.strip() or type, type, cfg,
                                enabled=enabled is not None)
    return RedirectResponse(f"/channels?message=Channel+{channel_id}+added",
                            status_code=303)


@app.post("/channels/{channel_id}/toggle", include_in_schema=False)
def channels_toggle(channel_id: int):
    channel = db.get_channel(channel_id)
    if channel is not None:
        db.set_channel_enabled(channel_id, not channel.get("enabled"))
    return RedirectResponse("/channels", status_code=303)


@app.post("/channels/{channel_id}/delete", include_in_schema=False)
def channels_delete(channel_id: int):
    db.delete_channel(channel_id)
    return RedirectResponse("/channels", status_code=303)


@app.post("/channels/{channel_id}/test", include_in_schema=False)
def channels_test(channel_id: int):
    """Send a test message through this channel (delivery may fail; the
    result is shown either way)."""
    status, error = channels.send_test(channel_id)
    if status == "sent":
        return RedirectResponse(
            "/channels?message=Test+notification+sent", status_code=303)
    from urllib.parse import quote

    return RedirectResponse(f"/channels?error={quote('Test failed: ' + error)}",
                            status_code=303)


@app.get("/approve/{token}", include_in_schema=False)
def approval_link_open(token: str):
    """Open a signed one-time approval link.

    First open redirects to the run (where the approval card lives); a
    reused or expired link is rejected with 410. The link never bypasses
    the role check on POST /runs/{id}/answer (section 1).
    """
    status, run_id = channels.consume_link(token)
    if status == "ok" and run_id:
        return RedirectResponse(f"/runs/{run_id}", status_code=303)
    messages = {
        "expired": "This approval link has expired.",
        "reused": "This approval link was already used.",
        "invalid": "This approval link is not valid.",
    }
    return HTMLResponse(
        html.page("Approval link",
                  '<h1 class="err">link rejected</h1>'
                  f'<p>{messages.get(status, messages["invalid"])}</p>'
                  '<p class="dim">Approval links are single-use. Ask for a '
                  'new notification if you still need to decide.</p>',
                  active="runs"),
        status_code=410 if status in ("expired", "reused") else 404,
    )


# --- skills (Phase 5) -------------------------------------------------------


def _skill_users() -> dict[str, list[str]]:
    """Map skill name -> agent node ids that attach it in the current flow."""
    users: dict[str, list[str]] = {}
    try:
        cfg = flowcfg.load_cfg()
    except Exception:
        return users
    for node in cfg.get("nodes", []):
        if node.get("type") != "agent":
            continue
        for name in node.get("skills") or []:
            users.setdefault(str(name), []).append(str(node.get("node_id")))
    return users


@app.get("/skills", response_class=HTMLResponse, include_in_schema=False)
def skills_page(request: Request) -> str:
    return html.skills_page(db.list_skills(), _skill_users(),
                            mode=_mode(request))


@app.post("/skills", response_class=HTMLResponse, include_in_schema=False)
def skills_add(name: str = Form(...), description: str = Form(""),
               body: str = Form(...)):
    name = name.strip()
    if not name:
        return html.skills_page(db.list_skills(), _skill_users(),
                                error="A skill needs a name.")
    if db.get_skill_by_name(name):
        return html.skills_page(db.list_skills(), _skill_users(),
                                error=f"A skill named '{name}' already exists.")
    if not body.strip():
        return html.skills_page(db.list_skills(), _skill_users(),
                                error="A skill needs a body.")
    db.add_skill(name, description, body)
    return html.skills_page(db.list_skills(), _skill_users(),
                            message=f"Added skill '{name}'.")


@app.post("/skills/{name}/delete", response_class=HTMLResponse,
          include_in_schema=False)
def skills_delete(name: str):
    users = _skill_users()
    if users.get(name):
        return html.skills_page(
            db.list_skills(), users,
            error=f"Skill '{name}' is attached to {', '.join(users[name])}; "
                  "detach it in the flow editor first.")
    db.delete_skill(name)
    return RedirectResponse("/skills", status_code=303)


# --- roles and permissions matrix (Phase 6) ---------------------------------

_RESOURCE_KINDS = {
    "tool:": "none, read, write",
    "field:": "none, read, write",
    "action:": "none, approve",
}


def _role_resources() -> list[str]:
    """The matrix columns: registered tools, data-model fields, named actions."""
    resources: set[str] = set()
    try:
        from ai_operator.tools.registry import names as tool_names
        resources.update(f"tool:{t}" for t in tool_names())
    except Exception:
        pass
    try:
        from ai_operator import datamodel
        for m in datamodel.load_models():
            for f in m.fields:
                resources.add(f"field:{m.name}.{f.name}")
    except Exception:
        pass
    resources.add("action:approve_payment")
    resources.add("action:start_run")  # Phase 6: gating API run starts
    return sorted(resources)


def _cycles() -> dict[str, list[str]]:
    return {
        "tool:": ["none", "read", "write"],
        "field:": ["none", "read", "write"],
        "action:": ["none", "write", "approve"],
    }


@app.get("/roles", response_class=HTMLResponse, include_in_schema=False)
def roles_page(request: Request, message: str = "", error: str = "") -> str:
    perms = db.list_role_permissions()
    return html.roles_page(
        db.list_roles(), perms, _role_resources(), _cycles(),
        message=message, error=error, mode=_mode(request))


@app.post("/roles", response_class=HTMLResponse, include_in_schema=False)
def roles_create(name: str = Form(...), description: str = Form("")) -> Response:
    name = name.strip()
    if not name:
        return html.roles_page(
            db.list_roles(), db.list_role_permissions(), _role_resources(),
            _cycles(), error="A role needs a name.")
    if db.get_role(name):
        return html.roles_page(
            db.list_roles(), db.list_role_permissions(), _role_resources(),
            _cycles(), error=f"A role named '{name}' already exists.")
    role_id = db.add_role(name, description)
    _audit_event("role_created", role=name, role_id=role_id)
    return RedirectResponse("/roles", status_code=303)


@app.post("/roles/{role_id}/delete", response_class=HTMLResponse,
          include_in_schema=False)
def roles_delete(role_id: int) -> Response:
    role = next((r for r in db.list_roles() if r["id"] == role_id), None)
    name = str((role or {}).get("name") or role_id)
    db.delete_role(role_id)
    _audit_event("role_deleted", role=name, role_id=role_id)
    return RedirectResponse("/roles", status_code=303)


@app.post("/roles/{role_id}/permissions", response_class=HTMLResponse,
          include_in_schema=False)
def roles_permission_toggle(role_id: int, resource: str = Form(...)) -> Response:
    role = next((r for r in db.list_roles() if r["id"] == role_id), None)
    role_name = str((role or {}).get("name") or role_id)
    perms = {p["resource"]: p["access"] for p in db.list_role_permissions()
             if p["role_id"] == role_id}
    kind = next((k for k in _cycles() if resource.startswith(k)), "tool:")
    cycle = _cycles()[kind]
    current = perms.get(resource, "none")
    nxt = cycle[(cycle.index(current) + 1) % len(cycle)]
    db.set_role_permission(role_id, resource, nxt)
    _audit_event("role_permission_changed", role=role_name, resource=resource,
                 old=current, new=nxt)
    return RedirectResponse("/roles", status_code=303)


# --- Phase 6 section 2: versioned JSON API for external agents --------------
#
# Every endpoint below runs under an API key: the key's role is subject to
# the same role checks as internal actors (section 1). The MCP server calls
# these endpoints, so the permission path is identical for both interfaces.

def _api_key(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.headers.get("x-api-key") or "").strip()


def _api_role(request: Request) -> str | None:
    """Role name for this request's key, or None when missing/invalid/revoked."""
    return db.api_key_role(_api_key(request))


def _api_401() -> JSONResponse:
    return JSONResponse({"detail": "missing or invalid API key"}, status_code=401)


def _token_totals(run_id: str) -> dict:
    """Live token totals from the trace's model_call events (same source as
    the report, so API and console always agree)."""
    total_in = total_out = 0
    cost = 0.0
    model = None
    for ev in _trace_events(run_id):
        if ev.get("event") != "model_call":
            continue
        total_in += int(ev.get("input_tokens") or 0)
        total_out += int(ev.get("output_tokens") or 0)
        cost += float(ev.get("estimated_cost_usd") or 0.0)
        model = ev.get("model") or model
    return {"input": total_in, "output": total_out,
            "total": total_in + total_out, "model": model,
            "estimated_cost_usd": round(cost, 6)}


def _trace_variables(run_id: str) -> dict:
    """Session variables as written during the run (variable_written events)."""
    out: dict = {}
    for ev in _trace_events(run_id):
        if ev.get("event") == "variable_written":
            out[str(ev.get("name"))] = ev.get("new")
    return out


def _pending_approvals() -> list[dict]:
    out: list[dict] = []
    for row in db.list_runs(200):
        if str(row.get("state") or "") != "waiting_approval":
            continue
        rid = str(row["run_id"])
        request_ev: dict = {}
        for ev in _trace_events(rid):  # last approval request wins
            if ev.get("event") == "approval_requested" and ev.get("kind") == "approval":
                request_ev = ev
        out.append({
            "run_id": rid,
            "task": row.get("task"),
            "session_id": row.get("session_id"),
            "tool_name": request_ev.get("tool_name"),
            "tool_args": request_ev.get("tool_args"),
            "reason": request_ev.get("reason"),
            "question": request_ev.get("question"),
        })
    return out


@app.post("/api/v1/runs")
def api_start_run(request: Request, payload: dict = Body(...)):
    """Start a run as the API key's role (POST /api/v1/runs)."""
    role = _api_role(request)
    if role is None:
        return _api_401()
    if not roles.allows(role, "action:start_run", "write"):
        return JSONResponse(
            {"detail": f"role '{role}' cannot start runs "
                       "(requires write on action:start_run)"},
            status_code=403)
    task = str(payload.get("task") or "").strip()
    session_id = payload.get("session_id")
    if not task or not isinstance(session_id, int):
        return JSONResponse(
            {"detail": "body must include task (string) and session_id (int)"},
            status_code=400)
    try:
        handle = runner.start_run(task, session_id)
    except RuntimeError as exc:  # another run active / bad session
        code = 404 if "No session" in str(exc) else 409
        return JSONResponse({"detail": str(exc)}, status_code=code)
    row = db.get_run(handle.run_id)
    return JSONResponse({"run_id": handle.run_id, "state": row.get("state"),
                         "status": row.get("status"), "task": task,
                         "session_id": session_id}, status_code=201)


@app.get("/api/v1/runs/{run_id}")
def api_run_detail(run_id: str, request: Request):
    """State, report, variables and token totals for one run."""
    if _api_role(request) is None:
        return _api_401()
    row = db.get_run(run_id)
    if row is None:
        return JSONResponse({"detail": f"no run '{run_id}'"}, status_code=404)
    return {
        "run_id": run_id,
        "state": row.get("state"),
        "status": row.get("status"),
        "task": row.get("task"),
        "session_id": row.get("session_id"),
        "source": row.get("source"),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
        "report": _report(run_id),
        "variables": _trace_variables(run_id),
        "tokens": _token_totals(run_id),
    }


@app.get("/api/v1/runs/{run_id}/events")
async def api_run_events(run_id: str, request: Request):
    if _api_role(request) is None:
        return _api_401()
    return StreamingResponse(_sse_gen(run_id),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                     "X-Accel-Buffering": "no"})


@app.post("/api/v1/runs/{run_id}/answer")
def api_answer_run(run_id: str, request: Request, payload: dict = Body(...)):
    """Answer an approval/clarification as the API key's role."""
    role = _api_role(request)
    if role is None:
        return _api_401()
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"detail": "answer must not be empty"},
                            status_code=400)
    can_approve, reason, _ = _approval_gate(run_id, answer, role)
    if not can_approve:
        return JSONResponse(
            {"detail": f"requires a higher role: {reason}"}, status_code=403)
    try:
        runner.answer_run(run_id, answer)
    except RuntimeError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return {"ok": True, "run_id": run_id}


def _approvals_response(request: Request, state: str) -> Response:
    if _api_role(request) is None:
        return _api_401()
    if state not in ("pending", "all"):
        return JSONResponse({"detail": "state must be 'pending' or 'all'"},
                            status_code=400)
    pending = _pending_approvals()
    return JSONResponse({"approvals": pending if state == "pending" else []})


@app.get("/api/v1/approvals")
def api_approvals(request: Request, state: str = "pending"):
    return _approvals_response(request, state)


@app.get("/approvals", include_in_schema=False)
def api_approvals_alias(request: Request, state: str = "pending"):
    """Spec-literal alias of /api/v1/approvals (same auth and payload)."""
    return _approvals_response(request, state)


# --- API keys page (Phase 6 section 2, configuration mode) -----------------

@app.get("/apikeys", response_class=HTMLResponse, include_in_schema=False)
def apikeys_page(request: Request, message: str = "", error: str = "",
                  fresh_key: str = "") -> str:
    return html.apikeys_page(db.list_api_keys(), db.list_roles(),
                             message=message, error=error, fresh_key=fresh_key,
                             mode=_mode(request))


@app.post("/apikeys", include_in_schema=False)
def apikeys_create(label: str = Form(...), role_id: int = Form(...)):
    label = label.strip()
    if not label:
        return HTMLResponse(html.apikeys_page(
            db.list_api_keys(), db.list_roles(), error="A key needs a label."),
            status_code=400)
    role = next((r for r in db.list_roles() if r["id"] == role_id), None)
    if role is None:
        return HTMLResponse(html.apikeys_page(
            db.list_api_keys(), db.list_roles(), error="Unknown role."),
            status_code=400)
    key_id, plain = db.create_api_key(label, role["id"])
    _audit_event("api_key_created", label=label, role=role["name"],
                 key_id=key_id)
    # The plain key is shown exactly once (only its hash is stored).
    return HTMLResponse(html.apikeys_page(
        db.list_api_keys(), db.list_roles(),
        message=f"Key '{label}' created - copy it now, it is not shown again.",
        fresh_key=plain))


@app.post("/apikeys/{key_id}/revoke", include_in_schema=False)
def apikeys_revoke(key_id: int):
    db.revoke_api_key(key_id)
    _audit_event("api_key_revoked", key_id=key_id)
    return RedirectResponse("/apikeys", status_code=303)


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
        detail = (list(errors.errors) if hasattr(errors, "errors")
                  else list(errors))
        return html.tool_badge(node_id, tool, tool in original,
                               note=detail[0] if detail else "invalid config")
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
    # Phase 6: a session's user_role must reference a real role.
    if not roles.role_exists(user_role.strip()):
        return HTMLResponse(
            html.sessions_page(
                db.list_sessions(),
                error=f"'{user_role.strip()}' is not a role in the roles "
                      "table (add it on the Roles page first)."),
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


def _audit_event(event_type: str, **fields: Any) -> None:
    """Append a platform (non-run) event so it shows on the audit trail.

    Config changes like role-permission toggles live in a synthetic
    RUNS_ROOT/_config/trace.jsonl read by the same _audit_events glob.
    """
    import time as _time

    try:
        RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        path = RUNS_ROOT / "_config" / "trace.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _time.time(),
            "time": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            "run_id": "_config",
            "event": event_type,
            **fields,
        }
        with path.open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


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
def flow_page(request: Request, message: str = "") -> str:
    return html.flow_page(flowcfg.load_cfg(), mode=_mode(request),
                          message=message)


@app.post("/flow/draft", response_class=HTMLResponse, include_in_schema=False)
def flow_draft(description: str = Form("")):
    """Phase 6 section 4: draft a flow from plain English (preview only).

    Never writes: the response is a diff + SVG preview with an Apply
    button, or the rejection list when two attempts failed validation.
    """
    cfg = flowcfg.load_cfg()
    if not description.strip():
        return HTMLResponse(html.draft_rejected(
            ["Describe the employee you want first."]))
    role = str((cfg.get("session_context") or {}).get("user_role") or "")
    draft, errors = drafting.draft_flow(description, cfg, role)
    if draft is None:
        return HTMLResponse(html.draft_rejected(errors))
    return HTMLResponse(html.draft_panel(cfg, draft))


@app.post("/flow/draft/apply", include_in_schema=False)
def flow_draft_apply(draft: str = Form(...)):
    """Apply a human-approved draft: validate-before-write, then record
    an "AI draft" version row for every changed agent."""
    try:
        draft_cfg = json.loads(draft)
    except ValueError:
        draft_cfg = None
    if not isinstance(draft_cfg, dict):
        return HTMLResponse(
            html.flow_page(flowcfg.load_cfg(),
                           errors=["The draft payload is not valid JSON."]),
            status_code=400)
    old_cfg = flowcfg.load_cfg()
    role = str((old_cfg.get("session_context") or {}).get("user_role") or "")
    # The human gate cannot bypass validation: re-check the draft against
    # the CURRENT config and role before anything is written.
    errors = drafting.check(draft_cfg, role)
    if errors:
        return HTMLResponse(html.flow_page(old_cfg, errors=errors),
                            status_code=400)
    changed = drafting.changed_agents(old_cfg, draft_cfg)
    errors = flowcfg.save_cfg(draft_cfg)  # authoritative validate-before-write
    if errors:
        return HTMLResponse(html.flow_page(old_cfg, errors=errors),
                            status_code=400)
    for node_id, previous in changed:
        db.add_version(node_id, previous, label="AI draft")
    return RedirectResponse("/flow?message=Draft+applied", status_code=303)


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
                   mismatch_next: str = Form(""),
                   skills: list[str] = Form([])) -> Response:
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
        # Phase 5: attaching/detaching skills is a normal config save, so it
        # creates an agent version row through the same path below.
        node["skills"] = skills
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


# --- prompt test panel (Phase 5) -------------------------------------------

_TEST_PROMPTS: dict[str, dict[str, str]] = {}
"""Last built prompt per node, for tests and debugging the test panel."""


@app.post("/flow/nodes/{node_id}/test", include_in_schema=False)
def flow_node_test(node_id: str, sample: str = Form("")):
    """Build a no-tools prompt from the agent's saved config and stream a reply.

    The response uses the run console's SSE shape: `data: {json}` lines with
    incremental text, ending with a done event. No tools or browser run.
    """
    cfg = flowcfg.load_cfg()
    node = next((n for n in cfg.get("nodes", [])
                 if str(n.get("node_id")) == node_id), None)
    if node is None:
        return HTMLResponse(html.not_found(f"No node '{node_id}'."),
                            status_code=404)
    if node.get("type") != "agent":
        return HTMLResponse(
            html.page("Flow editor",
                      '<h1 class="err">Only agent nodes can be tested.</h1>'
                      '<p><a href="/flow">Back</a></p>', active="flow"),
            status_code=400)
    system, user = compiler.build_test_prompt(node, sample)
    _TEST_PROMPTS[node_id] = {"system": system, "user": user}

    def event_stream():
        if os.environ.get("STUB_MODEL"):
            text = (f"(stub) {node_id} would reason about: "
                    f"{sample.strip() or 'the task'}.")
        else:
            try:
                text = get_client().complete(system, user)
            except Exception as exc:  # noqa: BLE001 - surface, never crash
                text = f"[test panel error] {exc!r}"
        for i in range(0, len(text), 80):
            yield f"data: {json.dumps({'text': text[i:i + 80]})}\n\n"
        yield 'data: {"done": true}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
