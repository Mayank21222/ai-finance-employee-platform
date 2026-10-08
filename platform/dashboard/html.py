"""Server-rendered HTML for the dashboard: plain f-strings + htmx, no template lib."""

from __future__ import annotations

import json
import time
from html import escape
from pathlib import Path

NAV = [
    ("runs", "/runs", "Run console", "run"),
    ("history", "/history", "History", "run"),
    ("audit", "/audit", "Audit trail", "run"),
    ("models", "/models", "Data models", "edit"),
    ("connectors", "/connectors", "Connectors", "edit"),
    ("schedules", "/schedules", "Schedules", "edit"),
    ("triggers", "/triggers", "Triggers", "edit"),
    ("channels", "/channels", "Channels", "edit"),
    ("skills", "/skills", "Skills", "edit"),
    ("roles", "/roles", "Roles", "edit"),
    ("apikeys", "/apikeys", "API keys", "edit"),
    ("sessions", "/sessions", "Sessions", "edit"),
    ("agents", "/agents", "Agents", "edit"),
    ("flow", "/flow", "Flow editor", "edit"),
    ("documents", "/documents", "Documents", "edit"),
    ("messages", "/messages", "Message nodes", "edit"),
]

MODES = ("use", "configure")

_CSS = """
  :root { --bg:#0f141a; --panel:#171e26; --line:#2a3441; --fg:#d7dee7;
          --dim:#8b98a7; --acc:#4da3ff; --ok:#3ecf8e; --warn:#f0b429;
          --err:#f07178; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  header { display:flex; align-items:center; gap:24px; padding:12px 20px;
           border-bottom:1px solid var(--line); background:var(--panel); }
  header .brand { font-weight:700; color:var(--acc); }
  nav a { color:var(--dim); text-decoration:none; margin-right:14px; }
  nav a:hover { color:var(--fg); }
  nav a.active { color:var(--fg); border-bottom:2px solid var(--acc);
                 padding-bottom:2px; }
  main { max-width:1100px; margin:24px auto; padding:0 20px; }
  h1 { font-size:20px; margin:0 0 16px; }
  h2 { font-size:15px; margin:24px 0 8px; color:var(--dim);
       text-transform:uppercase; letter-spacing:.06em; }
  table { width:100%; border-collapse:collapse; background:var(--panel);
          border:1px solid var(--line); border-radius:6px; overflow:hidden; }
  th, td { text-align:left; padding:8px 12px;
           border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:600; font-size:12px; }
  tr:last-child td { border-bottom:none; }
  input, textarea, select, button {
      background:#0c1116; color:var(--fg); border:1px solid var(--line);
      border-radius:5px; padding:7px 10px; font:inherit; }
  input:focus, textarea:focus { outline:1px solid var(--acc); }
  button { cursor:pointer; background:#1d2833; }
  button:hover { border-color:var(--acc); }
  button.primary { background:var(--acc); color:#06121f; border-color:var(--acc);
                   font-weight:600; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  table.matrix td button { font-size:11px; padding:3px 8px; }
  table.matrix button.btn-write { color:var(--ok); border-color:var(--ok); }
  table.matrix button.btn-approve { color:var(--warn); border-color:var(--warn); }
  table.matrix th { position:sticky; top:0; background:var(--panel); }
  .dim { color:var(--dim); }
  .err { color:var(--err); }
  .ok { color:var(--ok); }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
          font-size:12.5px; }
  .panel { background:var(--panel); border:1px solid var(--line);
           border-radius:6px; padding:14px; margin:12px 0; }
  .badge { display:inline-block; padding:1px 8px; border-radius:10px;
           font-size:11.5px; border:1px solid var(--line); color:var(--dim); }
  .badge.running, .badge.queued, .badge.waiting_approval { color:var(--warn);
       border-color:var(--warn); }
  .badge.complete, .badge.verified_complete, .badge.done,
  .badge.completed { color:var(--ok); border-color:var(--ok); }
  .badge.failed, .badge.needs_human { color:var(--err); border-color:var(--err); }
  .badge.interrupted { color:var(--err); border-color:var(--err); }
  .approval-card { border:1px solid var(--line); border-radius:6px;
                   padding:16px; background:#0a0e13; }
  .approval-card.pending { border-color:var(--warn); }
  .approval-card.approve { border-color:var(--ok); }
  .approval-card.reject { border-color:var(--err); }
  .approval-card .action { color:var(--fg); margin:6px 0; }
  .approval-card .policy { color:var(--warn); margin:4px 0; }
  .approval-card ul.summary { margin:8px 0 0; padding-left:18px;
                              color:var(--dim); }
  .approval-card .who { font-weight:600; margin-top:8px; }
  .approval-card .who.approve { color:var(--ok); }
  .approval-card .who.reject { color:var(--err); }
  .trace { background:#0a0e13; border:1px solid var(--line); border-radius:6px;
           padding:10px; height:340px; overflow-y:auto; font-size:12.5px;
           font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .trace .ev { padding:2px 0; border-bottom:1px dashed #1a222c; }
  .trace .ev b { color:var(--acc); }
  .trace .ev.error b, .trace .ev.tool_error b { color:var(--err); }
  .badge-tool { cursor:pointer; }
  .badge-tool.on { color:var(--ok); border-color:var(--ok); }
  .badge-tool.off { color:var(--dim); opacity:.55; }
  .mode-toggle { margin-left:auto; }
  .mode-toggle a { border:1px solid var(--acc); border-radius:5px;
                   padding:5px 12px; color:var(--acc); text-decoration:none; }
  .mode-toggle a:hover { background:var(--acc); color:#06121f; }
  /* Phase 4: usage vs configuration mode. The body data-mode attribute
     switches which sections (and nav links) are visible. */
  [data-mode="use"] .mode-edit { display:none; }
  [data-mode="configure"] .mode-run { display:none; }
  /* Phase 4: clickable SVG flow editor - diagram left, node panel right. */
  .flow-layout { display:flex; gap:16px; align-items:flex-start; }
  .flow-layout #diagram { flex:1 1 auto; overflow-x:auto; min-height:140px; }
  .flow-layout #node-panel { flex:0 0 360px; min-height:140px; }
  .fnode { cursor:pointer; }
  .fnode:hover .nshape { stroke:#fff; }
  /* Phase 4: audit trail - expandable rows, per-event-type labels. */
  .audit-row { border-bottom:1px dashed #1a222c; padding:2px 0; }
  .audit-row summary { cursor:pointer; padding:5px 0; font-size:13.5px; }
  .audit-row summary a { color:var(--acc); text-decoration:none; }
  .audit-row pre { background:#0a0e13; border:1px solid var(--line);
                   border-radius:6px; padding:8px; overflow-x:auto;
                   font-size:12px; }
  .lbl { display:inline-block; min-width:155px; font-size:12px;
         font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .lbl.l-write { color:var(--ok); }
  .lbl.l-err { color:var(--err); }
  .lbl.l-warn { color:var(--warn); }
  .lbl.l-info { color:var(--acc); }
  .lbl.l-dim { color:var(--dim); }
  /* Phase 5: agent edit panel tabs + version history. */
  .tab-row { gap:6px; margin:6px 0 10px; }
  .tab { border:1px solid var(--line); border-radius:5px; padding:4px 12px;
         color:var(--dim); text-decoration:none; font-size:13px;
         cursor:pointer; background:none; }
  .tab.active { color:var(--acc); border-color:var(--acc); }
  .ver-diff { color:var(--warn); font-size:12.5px; }
  .cmp-table td.changed { color:var(--warn); }
"""


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def page(title: str, body: str, active: str = "", mode: str = "use") -> str:
    mode = mode if mode in MODES else "use"
    nav = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""} '
        f'mode-{"run" if kind == "run" else "edit"}">'
        f"{label}</a>"
        for key, href, label, kind in NAV
    )
    other = "use" if mode == "configure" else "configure"
    toggle_label = "Use this app" if mode == "configure" else "Configure"
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Comp Ops</title>
<script src="/static/htmx.min.js"></script>
<style>{_CSS}</style>
</head><body data-mode="{mode}">
<header>
  <span class="brand">COMP OPS</span>
  <nav>{nav}</nav>
  <span class="mode-toggle"><a href="/mode/{other}">{toggle_label}</a></span>
</header>
<main>
{body}
</main>
</body></html>"""


def sessions_page(sessions: list[dict], error: str = "",
                  mode: str = "use") -> str:
    role_options = "".join(
        f'<option value="{esc(r)}"> {esc(r)}</option>'
        for r in _role_names()
    )
    rows = "".join(
        f"<tr><td>{s['id']}</td><td>{esc(s['tenant'])}</td>"
        f"<td>{esc(s['currency'])}</td><td>{s['approval_threshold']}</td>"
        f"<td>{esc(s['user_role'])}</td>"
        f'<td class="dim">{s["created_at"]:.0f}</td>'
        f'<td><form method="post" action="/sessions/{s["id"]}/delete" '
        f'onsubmit="return confirm(\'Delete session {s["id"]}?\')">'
        f"<button>Delete</button></form></td></tr>"
        for s in sessions
    )
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Sessions",
        f"""
<h1>Sessions</h1>
{err}
<table>
<tr><th>ID</th><th>Tenant</th><th>Currency</th><th>Approval threshold</th>
<th>User role</th><th>Created</th><th></th></tr>
{rows}
</table>
<h2>New session</h2>
<form method="post" action="/sessions" class="row">
  <input name="tenant" placeholder="tenant" required>
  <input name="currency" placeholder="currency" value="INR" required>
  <input name="approval_threshold" type="number" step="any" min="0"
         placeholder="threshold" value="50000" required>
  <select name="user_role">{role_options}</select>
  <button class="primary">Add session</button>
</form>
<p class="dim">Runs are linked to a session by ID; the session supplies
tenant, currency, approval threshold and user role (a real role from the
Roles page) at start.</p>
<h2>Tenant export</h2>
<p class="dim">Download everything for one tenant as a zip: flow config,
agent versions, skills, data models, roles, connectors (secrets redacted),
sessions, runs, traces and evidence. Restore it with
<code>fin import &lt;file.zip&gt;</code> into an empty database.</p>
<div class="row">{_tenant_export_links(sessions)}</div>""",
        active="sessions", mode=mode,
    )


def _tenant_export_links(sessions: list[dict]) -> str:
    tenants = sorted({str(s["tenant"]) for s in sessions})
    if not tenants:
        return '<span class="dim">No tenants yet.</span>'
    return "".join(
        f'<a href="/export/{esc(t)}"><button>Export {esc(t)} '
        f'(zip)</button></a>' for t in tenants)


def runs_page(runs: list[dict], sessions: list[dict],
              mode: str = "use") -> str:
    rows = "".join(
        f'<tr><td class="mono"><a href="/runs/{esc(r["run_id"])}" '
        f'style="color:var(--acc)">{esc(r["run_id"])}</a></td>'
        f'<td>{esc(r["task"] or "")}</td><td>{esc(r["tenant"] or "-")}</td>'
        f'<td><span class="badge {esc(r.get("state") or "-")}">'
        f'{esc(r.get("state") or "-")}</span></td>'
        f'<td><span class="badge {esc(r["status"])}">{esc(r["status"])}</span></td>'
        f'<td class="dim">{r["created_at"]:.0f}</td></tr>'
        for r in runs
    ) or '<tr><td colspan="6" class="dim">No runs yet.</td></tr>'
    options = "".join(
        f'<option value="{s["id"]}">#{s["id"]} {esc(s["tenant"])} '
        f'{esc(s["currency"])} &gt;{s["approval_threshold"]}</option>'
        for s in sessions
    )
    return page(
        "Run console",
        f"""
<h1>Run console</h1>
<div class="panel">
  <form method="post" action="/runs" class="row" id="start-run">
    <select name="session_id">{options}</select>
    <input name="task" style="flex:1;min-width:320px"
           placeholder="business request, e.g. process pending invoice INV-1042"
           required>
    <input name="failures" placeholder="failures (optional)"
           style="width:150px">
    <label class="dim"><input type="checkbox" name="fresh"> fresh</label>
    <button class="primary">Start run</button>
  </form>
</div>
<div id="active-run"></div>
<h2>Run history</h2>
<p class="dim">Full history with state, trace and evidence links lives on the
<a href="/history">History</a> page.</p>
<table>
<tr><th>Run</th><th>Task</th><th>Session</th><th>State</th><th>Status</th>
<th>Started</th></tr>
{rows}
</table>""",
        active="runs", mode=mode,
    )


def history_page(runs: list[dict], mode: str = "use") -> str:
    """Phase-3 runs history: state, task, session, when, trace/evidence links."""
    rows = ""
    for r in runs:
        state = str(r.get("state") or "-")
        started = time.strftime("%Y-%m-%d %H:%M:%S",
                                time.localtime(r["created_at"]))
        finished = (time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(r["finished_at"]))
                    if r.get("finished_at") else "-")
        rows += (
            f'<tr><td class="mono"><a href="/runs/{esc(r["run_id"])}" '
            f'style="color:var(--acc)">{esc(r["run_id"])}</a></td>'
            f'<td><span class="badge {esc(state)}">{esc(state)}</span></td>'
            f'<td>{esc(r["task"] or "")}<div class="dim">{esc(_source_cell(r))}</div></td>'
            f'<td>{esc(r["tenant"] or "-")} (#{r["session_id"] or "-"})</td>'
            f'<td class="dim">{esc(started)}</td>'
            f'<td class="dim">{esc(finished)}</td>'
            f'<td class="dim">{esc(_versions_cell(r))}</td>'
            f'<td class="dim mono">{esc(_tokens_cell(r))}</td>'
            f'<td><a href="/runs/{esc(r["run_id"])}/trace">trace</a> · '
            f'<a href="/runs/{esc(r["run_id"])}/evidence">evidence</a></td></tr>'
        )
    if not rows:
        rows = '<tr><td colspan="9" class="dim">No runs yet.</td></tr>'
    return page(
        "History",
        """
<h1>Runs history</h1>
<p class="dim">State: queued, running, waiting_approval (interrupted at an
approval, resumable), completed, failed, interrupted (cut short by a restart
or cancel).</p>
<table>
<tr><th>Run</th><th>State</th><th>Task</th><th>Session</th><th>Started</th>
<th>Finished</th><th>Agent versions</th><th>Tokens (est.)</th>
<th>Artifacts</th></tr>
""" + rows + "</table>",
        active="history", mode=mode,
    )


def _source_cell(run: dict) -> str:
    """Phase 5: manual vs scheduled, and why a scheduled run was skipped."""
    source = str(run.get("source") or "manual")
    parts = [f"source: {source}"]
    if run.get("skipped_reason"):
        parts.append(f"skipped: {run['skipped_reason']}")
    return " · ".join(parts)


def _tokens_cell(run: dict) -> str:
    """Phase 5: estimated token spend recorded in the run's report."""
    tok = run.get("tokens")
    if not tok:
        return "-"
    return (f"{int(tok.get('input', 0))}/{int(tok.get('output', 0))} "
            f"${float(tok.get('estimated_cost_usd', 0.0)):.4f}")


def _versions_cell(run: dict) -> str:
    """Phase 5: which agent configuration versions the run used."""
    raw = run.get("agent_versions_used")
    if not raw:
        return "-"
    try:
        mapping = json.loads(raw)
    except ValueError:
        return "-"
    if not mapping:
        return "-"
    return ", ".join(f"{n} v{v}" for n, v in sorted(mapping.items()))


def evidence_index_page(run_id: str, names: list[str], mode: str = "use") -> str:
    items = "".join(
        f'<li><a class="mono" href="/runs/{esc(run_id)}/evidence/'
        f'{esc(n)}">{esc(n)}</a></li>' for n in names
    ) or '<li class="dim">No evidence files.</li>'
    return page(
        f"Evidence {run_id}",
        f'<h1>Evidence · <span class="mono">{esc(run_id)}</span></h1>'
        f'<ul>{items}</ul>',
        active="history", mode=mode,
    )


def not_found(msg: str) -> str:
    return page("Not found", f'<h1 class="err">{esc(msg)}</h1>')


def event_line(ev: dict) -> str:
    kind = str(ev.get("event", "?"))
    ts = str(ev.get("time", ""))[-8:]
    skip = {"ts", "time", "run_id", "event"}
    detail = {k: v for k, v in ev.items() if k not in skip}
    blob = json.dumps(detail, default=str)
    if len(blob) > 400:
        blob = blob[:400] + "…"
    cls = "error" if kind in ("error", "tool_error", "visit_limit_hit") else ""
    return (f'<div class="ev {cls}"><span class="dim">{esc(ts)}</span> '
            f"<b>{esc(kind)}</b> {esc(blob)}</div>")


def tool_badge(node_id: str, tool: str, enabled: bool, note: str = "") -> str:
    cls = "on" if enabled else "off"
    label = tool if enabled else f"{tool} (off)"
    title = "Click to toggle; applies from the next run"
    if note:
        title = f"{note} (unchanged)"
    return (f'<span class="badge badge-tool {cls}" title="{esc(title)}" '
            f'hx-post="/agents/{esc(node_id)}/tools/{esc(tool)}" '
            f'hx-target="this" hx-swap="outerHTML">{esc(label)}</span>')


def answer_form(run_id: str, question: str, show: bool) -> str:
    hidden = "" if show else ' style="display:none"'
    return f"""<div class="panel" id="answer-panel"{hidden}>
  <form method="post" action="/runs/{esc(run_id)}/answer" class="row">
    <strong id="answer-question">{esc(question)}</strong>
    <input name="answer" style="flex:1;min-width:280px"
           placeholder="type your answer (y / n / a sentence)" required
           autofocus>
    <button class="primary">Submit answer</button>
  </form>
</div>"""


APPROVED_TOKENS = {"y", "yes", "approve", "approved"}


def _latest_approval(events: list[dict]) -> tuple[int | None, dict | None]:
    """The most recent approval interrupt in the trace (Phase 3: policy
    rule travels in the approval_requested event, not the text input)."""
    idx: int | None = None
    found: dict | None = None
    for i, ev in enumerate(events):
        if (ev.get("event") == "approval_requested"
                and ev.get("kind") == "approval"):
            idx, found = i, ev
    return idx, found


def _decision_after(events: list[dict], idx: int | None) -> dict | None:
    if idx is None:
        return None
    for ev in events[idx + 1:]:
        if ev.get("event") == "human_input":
            return ev
        if ev.get("event") in ("run_error", "cancelled_by_user"):
            return None
    return None


def _done_so_far(events: list[dict]) -> list[str]:
    """A compact 'what has the agent done' summary for the approval card."""
    lines: list[str] = []
    for ev in events:
        kind = str(ev.get("event"))
        if kind == "node_entered" and ev.get("node_type") == "agent":
            lines.append(f"entered agent {ev.get('node')}")
        elif kind == "tool_result":
            ok = "ok" if ev.get("ok") else "ERROR"
            args = json.dumps(ev.get("args") or {}, default=str)
            lines.append(f"{ev.get('tool')}({args}) -> {ok}")
        elif kind == "decision":
            lines.append(f"decided {ev.get('action_type')}: "
                         f"{str(ev.get('thought') or '')[:100]}")
        elif kind == "message_rendered":
            lines.append(f"message: {str(ev.get('text') or '')[:100]}")
        elif kind == "finish":
            lines.append(f"finish: {ev.get('status')}")
        elif kind == "evidence_captured":
            lines.append(f"evidence captured: {Path(str(ev.get('path'))).name}")
    tail = lines[-5:]
    if len(lines) > 5:
        tail.insert(0, "…")
    return tail


def approval_card(run_id: str, session: dict | None, events: list[dict]) -> str:
    """The approval as a decision card, not a text prompt (Phase 3).

    Server-rendered from the trace: the requested action (tool + data), the
    policy rule that triggered it, a summary of what the agent did so far,
    and Approve/Reject as hx-post buttons. Once decided, the card stays in
    the trace and shows the verdict and the approver (session user_role).
    """
    idx, req = _latest_approval(events)
    if req is None:
        return ""
    tool = str(req.get("tool_name") or "?")
    args = json.dumps(req.get("tool_args") or {}, default=str)
    reason = str(req.get("reason") or req.get("question") or "policy rule")
    decided = _decision_after(events, idx)
    who = (str(session.get("user_role"))
           if session and session.get("user_role") else "unknown")
    action = f'<p class="mono action">{esc(tool)}({esc(args)})</p>'
    policy = f'<p class="policy">policy rule: <strong>{esc(reason)}</strong></p>'
    summary = _done_so_far(events)
    summary_html = (""
                    if not summary else
                    '<p class="dim" style="margin:10px 0 0">done so far</p>'
                    f'<ul class="summary">'
                    + "".join(f"<li>{esc(line)}</li>" for line in summary)
                    + "</ul>")
    if decided is not None:
        answer = str(decided.get("answer") or "")
        approved = answer.strip().lower() in APPROVED_TOKENS
        verdict = "approved" if approved else "rejected"
        cls = "approve" if approved else "reject"
        when = str(decided.get("time") or "")[-19:]
        return (f'<div class="approval-card {cls}">'
                f'<h2 style="margin-top:0">approval {verdict}</h2>'
                f"{action}{policy}{summary_html}"
                f'<p class="who {cls}">{verdict.capitalize()} by '
                f"{esc(who)}<span class='dim'> · {esc(when)}</span></p>"
                "</div>")
    # Phase 6: the card checks the approver's role before offering Approve - an
    # auditor (or an ap_clerk past their limit) sees the reason up front, and
    # the same check rejects the POST in app.py (approval_denied_role).
    gate_reason = ""
    if session and session.get("user_role"):
        from ai_operator import roles
        amount = roles.approval_amount(req.get("tool_args"))
        can, gate_reason = roles.approve_gate(str(session["user_role"]), amount)
    else:
        can, gate_reason = True, ""
    approve_btn = (
        f'<button class="primary" hx-post="/runs/{esc(run_id)}/answer" '
        f'hx-vals=\'{{"answer": "approve"}}\' '
        f'hx-target="#approval-card-body" hx-swap="innerHTML">'
        f"Approve</button>"
        if can else
        f'<span class="badge" style="background:#f44336;color:#fff">'
        f"{esc(gate_reason)}</span>"
    )
    return (f'<div class="approval-card pending">'
            f'<h2 style="margin-top:0">approval requested'
            + (" — requires a higher role</h2>" if not can else "</h2>")
            + f"{action}{policy}{summary_html}"
            + (f'<p class="err">{esc(gate_reason or "requires a higher role")}</p>'
               if not can else "")
            + f'<div class="row" style="margin-top:12px">'
            + approve_btn
            + f'<button hx-post="/runs/{esc(run_id)}/answer" '
            + f'hx-vals=\'{{"answer": "reject"}}\' '
            + f'hx-target="#approval-card-body" hx-swap="innerHTML">'
            + f"Reject</button></div></div>")


def approval_panel_html(run_id: str, session: dict | None,
                        events: list[dict], visible: bool = False) -> str:
    """Live container for the approval card: hx-post buttons land here and
    hx-get polling keeps a decided card fresh without any page JS."""
    hidden = "" if visible else ' style="display:none"'
    return (f'<div class="panel" id="approval-panel"{hidden}>'
            f'<div id="approval-card-body" '
            f'hx-get="/runs/{esc(run_id)}/approval-card" '
            f'hx-trigger="load, every 2000ms" hx-swap="innerHTML">'
            f"{approval_card(run_id, session, events)}"
            f"</div></div>")


def resume_form(run_id: str, question: str) -> str:
    """Phase 3: re-enter a checkpointed waiting_approval run after a restart."""
    return f"""<div class="panel" id="resume-panel">
  <h2>run interrupted by a dashboard restart</h2>
  <p class="dim">This run stopped at an approval and its checkpoint is saved.
  Submit the approval answer to resume from exactly where it left off.</p>
  <form method="post" action="/runs/{esc(run_id)}/resume" class="row">
    <strong>{esc(question or "Approve?")}</strong>
    <input name="answer" style="flex:1;min-width:280px"
           placeholder="type your answer (y / n / a sentence)" required
           autofocus>
    <button class="primary">Resume run</button>
  </form>
</div>"""


def run_detail_page(run_id: str, task: str, status: str, session: dict | None,
                    waiting: bool, question: str, report: dict | None,
                    error: str | None, agents_html: str, run_active: bool,
                    state: str = "", resume: bool = False,
                    resume_question: str = "",
                    approval_panel: str = "", mode: str = "use") -> str:
    sess = (
        f'#{session["id"]} {esc(session["tenant"])} {esc(session["currency"])} '
        f'&gt;{session["approval_threshold"]} ({esc(session["user_role"])})'
        if session else "-"
    )
    cancel = (
        f'<form method="post" action="/runs/{esc(run_id)}/cancel" '
        f'onsubmit="return confirm(\'Cancel this run?\')">'
        f"<button>Cancel run</button></form>"
        if run_active else ""
    )
    report_html = ""
    if report is not None:
        items = []
        for key in ("status", "summary"):
            if report.get(key):
                items.append(f"<p><strong>{esc(key)}:</strong> "
                             f"{esc(report[key])}</p>")
        for key in ("errors", "approvals", "actions"):
            vals = report.get(key) or []
            if vals:
                lis = "".join(f"<li>{esc(v)}</li>" for v in vals)
                items.append(f'<h2>{esc(key)}</h2><ul>{lis}</ul>')
        ev = report.get("evidence") or []
        if ev:
            links = "".join(
                f'<li><a class="mono" target="_blank" '
                f'href="/runs/{esc(run_id)}/evidence/{esc(Path(str(p)).name)}">'
                f"{esc(Path(str(p)).name)}</a></li>"
                for p in ev
            )
            items.append(f"<h2>evidence</h2><ul>{links}</ul>")
        tok = report.get("tokens")
        if tok:
            items.append(
                "<p><strong>model usage (estimated):</strong> "
                f"{int(tok.get('input', 0))} in / {int(tok.get('output', 0))} out "
                f"tokens &middot; ${float(tok.get('estimated_cost_usd', 0.0)):.4f} "
                f"&middot; {esc(tok.get('model') or 'unknown')}</p>"
            )
        report_html = ('<div class="panel" id="report"><h2>final report</h2>'
                       + "".join(items) + "</div>")
    err_html = (f'<p class="err">{esc(error)}</p>' if error else "")
    poll = ('hx-get="/runs/%s/agents" hx-trigger="load, every 2000ms"'
            % esc(run_id)) if run_active else ""
    script = f"""<script>
(function() {{
  const traceEl = document.getElementById("trace");
  const panel = document.getElementById("answer-panel");
  const qEl = document.getElementById("answer-question");
  const stateEl = document.getElementById("state-badge");
  const tokenEl = document.getElementById("token-total");
  const tokens = {{input: 0, output: 0, cost: 0.0}};
  const setState = function(s) {{
    if (stateEl) {{ stateEl.className = "badge " + s; stateEl.textContent = s; }}
  }};
  const setTokens = function() {{
    if (!tokenEl) return;
    tokenEl.style.display = "";
    tokenEl.textContent = "tokens " + tokens.input + " in / " + tokens.output +
      " out · est. $" + tokens.cost.toFixed(4);
  }};
  const es = new EventSource("/runs/{esc(run_id)}/events");
  es.onmessage = function(m) {{
    let ev; try {{ ev = JSON.parse(m.data); }} catch (e) {{ return; }}
    if (ev.event === "__done__") {{
      es.close();
      setTimeout(function() {{ location.reload(); }}, 500);
      return;
    }}
    const div = document.createElement("div");
    div.className = "ev";
    const time = (ev.time || "").slice(-8);
    const skip = {{ts:1, time:1, run_id:1, event:1}};
    const detail = Object.keys(ev).filter(function(k) {{ return !skip[k]; }})
      .map(function(k) {{ return k + "=" + JSON.stringify(ev[k]); }})
      .join(" ");
    div.innerHTML = '<span class="dim">' + time + '</span> <b>' +
      String(ev.event).replace(/[<>&]/g, "") + '</b> ' +
      String(detail).replace(/</g, "&lt;");
    traceEl.appendChild(div);
    traceEl.scrollTop = traceEl.scrollHeight;
    if (ev.event === "approval_requested") {{
      setState("waiting_approval");
      const ap = document.getElementById("approval-panel");
      if (ap) ap.style.display = "";
    }}
    if (ev.event === "human_input_requested") {{
      setState("waiting_approval");
      if (qEl) qEl.textContent = ev.question || JSON.stringify(ev);
      if (panel) panel.style.display = "";
    }}
    if (ev.event === "human_input") {{
      setState("running");
      if (panel) panel.style.display = "none";
    }}
    if (ev.event === "model_call") {{
      tokens.input += Number(ev.input_tokens || 0);
      tokens.output += Number(ev.output_tokens || 0);
      tokens.cost += Number(ev.estimated_cost_usd || 0);
      setTokens();
    }}
  }};
}})();
</script>"""
    state_badge = (f'<span class="badge {esc(state)}" id="state-badge">'
                   f"{esc(state)}</span>") if state else ""
    resume_html = resume_form(run_id, resume_question) if resume else ""
    body = f"""<div class="row" style="justify-content:space-between">
  <h1>Run <span class="mono">{esc(run_id)}</span></h1>
  <div class="row">
    {state_badge}
    <span class="badge {esc(status)}">{esc(status)}</span>
    {cancel}
    <a href="/runs"><button>Back</button></a>
  </div>
</div>
<p>{esc(task)}</p>
<p class="dim">session: {sess}</p>
{err_html}
{answer_form(run_id, question, waiting)}
{resume_html}
{approval_panel}
<h2>live trace <span class="badge" id="token-total" style="display:none"></span></h2>
<div class="trace" id="trace"></div>
<h2>agents</h2>
<div id="agents" {poll}>{agents_html}</div>
{report_html}
{script}"""
    return page(f"Run {run_id}", body, active="runs", mode=mode)


def agents_fragment(agents: list[dict], run_active: bool) -> str:
    if not agents:
        return ('<p class="dim">This flow has no agent nodes. '
                "Agent cards appear for flows with agent nodes.</p>")
    rows = []
    for a in agents:
        status = a["status"]
        reasoning = a.get("reasoning") or ""
        answer = a.get("answer") or ""
        badges = "".join(
            tool_badge(a["node_id"], t, t in a["tools"])
            for t in a["all_tools"]
        )
        rows.append(
            f'<tr><td><strong>{esc(a["node_id"])}</strong>'
            f'<div class="dim mono">{esc(a.get("save_as") or "")}</div></td>'
            f'<td><span class="badge {esc(status)}">{esc(status)}</span></td>'
            f'<td class="mono" style="max-width:340px">{esc(reasoning[:300])}</td>'
            f'<td class="mono">{esc(answer)}</td>'
            f'<td class="row">{badges}</td></tr>'
        )
    head = ("<tr><th>Agent</th><th>Status</th><th>Current reasoning</th>"
            "<th>Answer</th><th>Tools (click toggles)</th></tr>")
    note = ('<p class="dim">Live while a run is active; tool toggles apply '
            "from the next run.</p>") if run_active else ""
    return f"<table>{head}{''.join(rows)}</table>{note}"


def mermaid_diagram(cfg: dict) -> str:
    lines = ["flowchart LR"]
    node_ids = [n["node_id"] for n in cfg.get("nodes", [])]

    def mid(value: str) -> str:
        return str(value).replace('"', "")

    for n in cfg.get("nodes", []):
        nid = mid(n["node_id"])
        kind = n.get("type", "?")
        if kind == "verify":
            lines.append(f'    {nid}{{"{nid}<br/>verify"}}')
        elif kind == "message":
            lines.append(f'    {nid}("{nid}<br/>message")')
        else:
            lines.append(f'    {nid}["{nid}<br/>{kind}"]')
    start = mid(cfg.get("start_node_id", ""))
    if start in node_ids:
        lines.append(f"    start([start]) --> {start}")
    for n in cfg.get("nodes", []):
        nid = mid(n["node_id"])
        for target in n.get("next_node_ids") or []:
            if str(target) in node_ids:
                lines.append(f"    {nid} --> {mid(target)}")
        fb = n.get("fallback_next")
        if fb and str(fb) in node_ids:
            lines.append(f"    {nid} -. fallback .-> {mid(fb)}")
        if n.get("type") == "verify":
            lines.append(f'    {nid} -- match --> {mid(n["match_next"])}')
            lines.append(f'    {nid} -- mismatch --> {mid(n["mismatch_next"])}')
    return "\n".join(lines)


def validation_panel(errors=None) -> str:
    """Blocking errors and guardrail warnings, rendered separately.

    Accepts a ValidationResult (Phase 6 lint), a plain error list (legacy
    callers), or None for the idle hint. Warnings are never blocking.
    """
    if errors is None:
        return ('<div class="panel" id="validation"><span class="dim">'
                "Click ‘Validate flow’ to check the config.</span></div>")
    warning_list: list[str] = []
    if hasattr(errors, "errors"):  # ValidationResult
        warning_list = list(getattr(errors, "warnings", []) or [])
        errors = list(errors.errors)
    else:
        errors = list(errors or [])
    if not errors and not warning_list:
        return ('<div class="panel" id="validation">'
                '<span class="ok">Flow is valid.</span></div>')
    parts = []
    if errors:
        lis = "".join(f"<li>{esc(e)}</li>" for e in errors)
        parts.append(
            f'<span class="err">Flow has {len(errors)} blocking problem(s):</span>'
            f"<ul>{lis}</ul>")
    else:
        parts.append('<span class="ok">Flow is valid.</span>')
    if warning_list:
        lis = "".join(f"<li>{esc(w)}</li>" for w in warning_list)
        parts.append(
            f'<span style="color:var(--warn)">Guardrail score: '
            f'{len(warning_list)} warning(s) (non-blocking):</span>'
            f"<ul>{lis}</ul>")
    return ('<div class="panel" id="validation">' + "".join(parts) + "</div>")


NODE_STROKE = {"agent": "#4da3ff", "message": "#3ecf8e",
               "verify": "#f0b429", "end": "#8b98a7"}


def flow_svg(cfg: dict) -> str:
    """Server-generated clickable SVG of the flow (Phase 4 WYSIWYG editor).

    Fixed layout: nodes in config order top-to-bottom, edges routed as
    straight lines through right-hand lanes (solid = next, dashed =
    fallback, labeled = verify match/mismatch). Each node shape carries
    hx-get so a click swaps that node's edit form into #node-panel.
    """
    nodes = cfg.get("nodes", [])
    ids = [str(n.get("node_id")) for n in nodes]
    if not ids:
        return '<p class="dim">This flow has no nodes to draw.</p>'
    x, w, h, gap = 80, 240, 52, 46
    pitch, top = h + gap, 40
    idx = {nid: i for i, nid in enumerate(ids)}

    edges: list[tuple[str, str, str, bool]] = []
    for n in nodes:
        nid = str(n["node_id"])
        nexts = [str(t) for t in (n.get("next_node_ids") or [])]
        for target in nexts:
            if target in idx:
                edges.append((nid, target, "", False))
        fb = n.get("fallback_next")
        if fb and str(fb) in idx and str(fb) not in nexts:
            edges.append((nid, str(fb), "fallback", True))
        if n.get("type") == "verify":
            for key, label in (("match_next", "match"),
                               ("mismatch_next", "mismatch")):
                target = n.get(key)
                if target and str(target) in idx:
                    edges.append((nid, str(target), label, False))

    lane0 = x + w + 30
    width = lane0 + 14 * len(edges) + 96
    height = top + len(ids) * pitch + 16
    out = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="flow diagram">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#4a5768"/></marker></defs>',
    ]

    def cy(nid: str) -> int:
        return top + idx[nid] * pitch + h // 2

    for k, (src, dst, label, dashed) in enumerate(edges):
        lane = lane0 + k * 14
        y1, y2 = cy(src), cy(dst)
        if src == dst:
            path = f"M {x + w} {y1 - 10} H {lane} V {y1 + 10} H {x + w}"
            mid = y1
        else:
            path = f"M {x + w} {y1} H {lane} V {y2} H {x + w}"
            mid = (y1 + y2) // 2
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        out.append(f'<path d="{path}" fill="none" stroke="#4a5768" '
                   f'stroke-width="1.5"{dash} marker-end="url(#arrow)"/>')
        if label:
            out.append(f'<text x="{lane + 5}" y="{mid - 4}" fill="#8b98a7" '
                       f'font-size="10">{esc(label)}</text>')

    start = str(cfg.get("start_node_id", ""))
    if start in idx:
        sy = cy(start)
        out.append(f'<ellipse cx="36" cy="{sy}" rx="28" ry="14" '
                   'fill="#171e26" stroke="#8b98a7"/>')
        out.append(f'<text x="36" y="{sy + 4}" text-anchor="middle" '
                   'fill="#d7dee7" font-size="11">start</text>')
        out.append(f'<path d="M 64 {sy} H {x}" fill="none" stroke="#4a5768" '
                   'stroke-width="1.5" marker-end="url(#arrow)"/>')

    for n in nodes:
        nid = str(n["node_id"])
        kind = str(n.get("type", "?"))
        y = top + idx[nid] * pitch
        stroke = NODE_STROKE.get(kind, "#8b98a7")
        common = (f'fill="#1c2733" stroke="{stroke}" stroke-width="1.5" '
                  'class="nshape"')
        if kind == "verify":
            pts = (f"{x},{y + h // 2} {x + w * 0.15},{y} {x + w * 0.85},{y} "
                   f"{x + w},{y + h // 2} {x + w * 0.85},{y + h} "
                   f"{x + w * 0.15},{y + h}")
            shape = f'<polygon points="{pts}" {common}/>'
        else:
            rx = h // 2 if kind == "end" else (16 if kind == "message" else 8)
            shape = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                     f'rx="{rx}" {common}/>')
        sub = kind if not n.get("save_as") else \
            f"{kind} · {n.get('save_as')}"
        body = (
            shape
            + f'<text x="{x + w // 2}" y="{y + 22}" text-anchor="middle" '
              f'fill="#d7dee7" font-size="13" font-weight="600">{esc(nid)}</text>'
            + f'<text x="{x + w // 2}" y="{y + 39}" text-anchor="middle" '
              f'fill="#8b98a7" font-size="10">{esc(sub)}</text>'
        )
        out.append(
            f'<g class="fnode" hx-get="/flow/nodes/{esc(nid)}" '
            f'hx-target="#node-panel" hx-swap="innerHTML">'
            f"<title>{esc(nid)} - click to edit</title>{body}</g>"
        )
    out.append("</svg>")
    return "".join(out)


def _skill_names() -> list[str]:
    """Names of skills available for attachment (lazy DB read; [] on error)."""
    try:
        from platform.dashboard import db

        return [s["name"] for s in db.list_skills()]
    except Exception:
        return []


def _role_names() -> list[str]:
    """Role names for selects and the matrix (lazy DB read; [] on error)."""
    try:
        from platform.dashboard import db

        return [r["name"] for r in db.list_roles()]
    except Exception:
        return []


def node_edit_panel(cfg: dict, node_id: str, message: str = "",
                    error: str = "") -> str:
    """The click-to-edit form for one node, swapped into #node-panel."""
    node = next((n for n in cfg.get("nodes", [])
                 if str(n.get("node_id")) == node_id), None)
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    if node is None:
        return (f'<div class="panel" id="node-panel">{msg}{err}'
                f'<p class="err">No node named {esc(node_id)} in this flow.'
                "</p></div>")
    kind = str(node.get("type", "?"))
    ids = [str(n["node_id"]) for n in cfg.get("nodes", [])]

    def options(selected: str) -> str:
        return "".join(
            f'<option value="{esc(i)}" {"selected" if i == selected else ""}>'
            f"{esc(i)}</option>" for i in ids)

    form_open = (f'<form hx-post="/flow/nodes/{esc(node_id)}" '
                 'hx-target="#node-panel" hx-swap="innerHTML">')
    extra = ""
    if kind == "agent":
        badges = "".join(
            tool_badge(node_id, t, t in (node.get("tools_enabled") or []))
            for t in _registry_names())
        docs = ", ".join(node.get("documents") or []) or "none"
        known_skills = _skill_names()
        attached = list(node.get("skills") or [])
        if known_skills:
            boxes = "".join(
                f'<label class="dim"><input type="checkbox" name="skills" '
                f'value="{esc(s)}" {"checked" if s in attached else ""}>'
                f"{esc(s)}</label>"
                for s in known_skills
            )
            skill_html = ('<p><label class="dim">Skills (attach/detach)'
                          f"</label></p><p class='row'>{boxes}</p>")
        else:
            skill_html = ('<p class="dim">No skills in the library yet - add '
                          "some on the Skills page to attach them here.</p>")
        fields = f"""
    <p><label class="dim">System prompt (file path or inline text)</label>
    <textarea name="system_prompt" rows="4" style="width:100%"
      >{esc(node.get("system_prompt") or "")}</textarea></p>
    <p><label class="dim">Instructions</label>
    <textarea name="instructions" rows="6" style="width:100%"
      >{esc(node.get("instructions") or "")}</textarea></p>
    {skill_html}
    <div class="row">
      <label class="dim">Save answer as
        <input name="save_as" value="{esc(node.get("save_as") or "")}"></label>
      <label class="dim">Fallback next
        <select name="fallback_next"
          >{options(str(node.get("fallback_next") or ""))}</select></label>
      <button class="primary">Save node</button>
    </div>"""
        test_panel = f"""
<div class="panel" style="margin-top:12px">
  <h2 style="margin:0">Test this agent</h2>
  <p class="dim">Builds a prompt from this node's saved config - system prompt,
  attached skills, data model - with no tools, and streams the reply.</p>
  <p><textarea id="test-sample" rows="3" style="width:100%"
    placeholder="sample task, e.g. check invoice INV-1"></textarea></p>
  <button type="button" class="primary" id="test-run"
    onclick="runNodeTest('{esc(node_id)}')">Run test</button>
  <pre id="test-output" class="mono"
    style="white-space:pre-wrap;margin-top:8px"></pre>
</div>"""
        extra = (f'<p class="dim">Attached documents: {esc(docs)}</p>'
                 f'<p class="row">{badges}</p>'
                 f"{test_panel}")
    elif kind == "message":
        primary = str((node.get("next_node_ids") or [""])[0])
        fields = f"""
    <p><label class="dim">Template</label>
    <textarea name="template" rows="4" style="width:100%"
      >{esc(node.get("template") or "")}</textarea></p>
    <div class="row">
      <label class="dim">Next
        <select name="next_node_id">{options(primary)}</select></label>
      <label class="dim">Fallback when unset
        <select name="fallback_next"
          >{options(str(node.get("fallback_next") or ""))}</select></label>
      <button class="primary">Save node</button>
    </div>"""
        extra = ('<p class="dim">A missing variable renders a visible '
                 "template error and routes to the fallback.</p>")
    elif kind == "verify":
        fields = f"""
    <div class="row">
      <label class="dim">On match
        <select name="match_next"
          >{options(str(node.get("match_next") or ""))}</select></label>
      <label class="dim">On mismatch
        <select name="mismatch_next"
          >{options(str(node.get("mismatch_next") or ""))}</select></label>
      <button class="primary">Save node</button>
    </div>"""
        extra = ('<p class="dim">The verifier checks against the attached '
                 "data model, never the prompt.</p>")
    else:
        fields = ('<p class="dim">End nodes only finish the run - '
                  "nothing to edit.</p>")
    tabs = _node_tabs(node_id, "edit") if kind == "agent" else ""
    return f"""
<div class="panel" id="node-panel">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(node_id)}</h2>
    <span class="badge">{esc(kind)}</span>
  </div>
  {tabs}
  {msg}{err}
  {form_open}{fields}
  </form>
  {extra}
</div>"""


def _node_tabs(node_id: str, active: str) -> str:
    """Edit / Versions tabs; both swap the whole panel via HTMX."""
    edit_cls = "tab active" if active == "edit" else "tab"
    ver_cls = "tab active" if active == "versions" else "tab"
    return (
        f'<div class="row tab-row">'
        f'<a class="{edit_cls}" hx-get="/flow/nodes/{esc(node_id)}" '
        f'hx-target="#node-panel" hx-swap="innerHTML">Edit</a>'
        f'<a class="{ver_cls}" hx-get="/flow/nodes/{esc(node_id)}/versions" '
        f'hx-target="#node-panel" hx-swap="innerHTML">Versions</a></div>'
    )


def _version_diff(old: dict, new: dict) -> str:
    """Which top-level fields differ between two configuration snapshots."""
    keys = sorted(set(old) | set(new))
    changed = [k for k in keys if old.get(k) != new.get(k)]
    return ", ".join(changed) if changed else "(no field changed)"


def node_versions_panel(node_id: str, versions: list[dict],
                        message: str = "", error: str = "") -> str:
    """Phase 5: the version-history tab of an agent's edit panel."""
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    rows = ""
    for i, v in enumerate(versions):  # newest first
        older = versions[i + 1]["config"] if i + 1 < len(versions) else None
        diff = ("(initial configuration)" if older is None
                else _version_diff(older, v["config"]))
        label = f' <span class="badge">{esc(v["label"])}</span>' if v.get("label") else ""
        rows += (
            f'<tr><td class="mono">v{v["version"]}</td>'
            f'<td class="dim">{esc(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v["created_at"])))}</td>'
            f'<td>{esc(v.get("label") or "-")}{label}</td>'
            f'<td class="ver-diff">{esc(diff)}</td>'
            f'<td><a class="tab" hx-get="/flow/nodes/{esc(node_id)}/versions/'
            f'{v["version"]}" hx-target="#node-panel" '
            f'hx-swap="innerHTML">Compare</a> '
            f'<button class="tab" hx-post="/flow/nodes/{esc(node_id)}/versions/'
            f'{v["version"]}/restore" hx-target="#node-panel" '
            f'hx-swap="innerHTML" '
            f'hx-confirm="Restore v{v["version"]}? The current config is kept '
            f'in history.">Restore</button></td></tr>'
        )
    if not rows:
        rows = ('<tr><td colspan="5" class="dim">No versions yet - every '
                "save from this panel records the configuration it "
                "replaced.</td></tr>")
    current = len(versions) + 1  # the live config is the next version number
    return f"""
<div class="panel" id="node-panel">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(node_id)}</h2>
    <span class="badge">agent</span>
  </div>
  {_node_tabs(node_id, "versions")}
  {msg}{err}
  <p class="dim">Version history (newest first). The live configuration is
  <strong>v{current}</strong>; each row below kept the configuration a save
  replaced. Restores are recorded as new versions too.</p>
  <table class="cmp-table"><tr><th>Version</th><th>When</th><th>Label</th>
  <th>Fields changed</th><th></th></tr>{rows}</table>
</div>"""


def node_version_compare(node_id: str, current: dict, snapshot: dict) -> str:
    """Side-by-side field comparison: live config vs one version snapshot."""
    keys = sorted(set(current) | set(snapshot))
    rows = ""
    for key in keys:
        if key in ("type",):
            continue
        cur = json.dumps(current.get(key), default=str)
        old = json.dumps(snapshot.get(key), default=str)
        cls = ' class="changed"' if cur != old else ""
        rows += (f"<tr><td class='mono'>{esc(key)}</td>"
                 f"<td{cls}><pre>{esc(cur[:600])}</pre></td>"
                 f"<td{cls}><pre>{esc(old[:600])}</pre></td></tr>")
    return f"""
<div class="panel" id="node-panel">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(node_id)}</h2>
    <span class="badge">v{snapshot["version"]}</span>
  </div>
  {_node_tabs(node_id, "versions")}
  <p class="dim">Comparing the live configuration with v{snapshot["version"]}
  {esc("(" + snapshot["label"] + ")" if snapshot.get("label") else "")}.
  Highlighted rows differ.</p>
  <table class="cmp-table"><tr><th>Field</th><th>Current</th>
  <th>v{snapshot["version"]}</th></tr>{rows}</table>
  <p><a class="tab" hx-get="/flow/nodes/{esc(node_id)}/versions"
     hx-target="#node-panel" hx-swap="innerHTML">Back to versions</a>
  <button class="tab" hx-post="/flow/nodes/{esc(node_id)}/versions/{snapshot["version"]}/restore"
   hx-target="#node-panel" hx-swap="innerHTML"
   hx-confirm="Restore v{snapshot["version"]}?">Restore this version</button></p>
</div>"""


def _draft_row(old: dict, new: dict) -> tuple[str, str]:
    """(status, changed-detail) for one node in the draft diff."""
    if old is None:
        return "added", ""
    if new is None:
        return "removed", ""
    if old == new:
        return "unchanged", ""
    keys = [k for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
    bits = []
    for k in keys[:4]:
        a, b = str(old.get(k) or ""), str(new.get(k) or "")
        bits.append(f"{k}: {a[:40]} \u2192 {b[:40]}")
    more = f" (+{len(keys) - 4} more)" if len(keys) > 4 else ""
    return "changed", "; ".join(bits) + more


def draft_diff(old_cfg: dict, draft: dict) -> str:
    """Side-by-side diff of the current flow against the draft."""
    old_nodes = {n.get("node_id"): n for n in old_cfg.get("nodes") or []}
    new_nodes = {n.get("node_id"): n for n in draft.get("nodes") or []}
    rows = ""
    for nid in list(old_nodes) + [i for i in new_nodes if i not in old_nodes]:
        status, detail = _draft_row(old_nodes.get(nid), new_nodes.get(nid))
        colour = {"added": "ok", "removed": "err",
                  "changed": "warn"}.get(status, "dim")
        old_cell = (json.dumps(old_nodes[nid], indent=1)[:400]
                    if nid in old_nodes else "(absent)")
        new_cell = (json.dumps(new_nodes[nid], indent=1)[:400]
                    if nid in new_nodes else "(absent)")
        rows += (
            f'<tr><td class="mono"><strong>{esc(nid)}</strong></td>'
            f'<td><span class="badge" style="color:var(--{colour})">'
            f'{status}</span></td>'
            f'<td class="mono" style="white-space:pre-wrap">{esc(old_cell)}'
            f'</td><td class="mono" style="white-space:pre-wrap">'
            f'{esc(new_cell)}</td><td class="dim">{esc(detail)}</td></tr>')
    # flow-level changes (start node, session context, limits)
    for key in ("start_node_id", "session_context", "max_visits_per_node",
                "max_total_visits"):
        a, b = old_cfg.get(key), draft.get(key)
        if a != b:
            rows += (
                f'<tr><td class="mono"><strong>(flow)</strong> {esc(key)}'
                f'</td><td><span class="badge" style="color:var(--warn)">'
                f'changed</span></td>'
                f'<td class="mono">{esc(json.dumps(a))[:400]}</td>'
                f'<td class="mono">{esc(json.dumps(b))[:400]}</td>'
                f'<td></td></tr>')
    if not rows:
        rows = '<tr><td colspan="5" class="dim">No differences.</td></tr>'
    return (
        '<table><tr><th>Node</th><th>Status</th><th>Current</th>'
        f'<th>Draft</th><th>Changes</th></tr>{rows}</table>')


def draft_panel(old_cfg: dict, draft: dict) -> str:
    """Phase 6 section 4: preview of a valid draft - nothing saved yet."""
    return f"""
<div class="panel">
<p><span class="badge completed">draft ready</span> This is a preview only:
nothing is saved until you click Apply.</p>
<h3>Diff against the current flow</h3>
{draft_diff(old_cfg, draft)}
<h3>Draft preview (SVG editor)</h3>
<div class="panel">{flow_svg(draft)}</div>
<form method="post" action="/flow/draft/apply" class="row">
  <input type="hidden" name="draft" value="{esc(json.dumps(draft))}">
  <button class="primary">Apply draft</button>
  <span class="dim">Applying saves the config and records an "AI draft"
  version row for every changed agent.</span>
</form>
</div>"""


def draft_rejected(errors: list[str]) -> str:
    """A draft that failed validation twice - nothing was applied."""
    items = "".join(f"<li>{esc(e)}</li>" for e in errors)
    return f"""
<div class="panel">
<p><span class="badge interrupted">draft rejected</span> Nothing was applied
to the current flow.</p>
<ul class="err">{items}</ul>
</div>"""


def flow_page(cfg: dict, errors: list[str] | None = None,
              mode: str = "use", message: str = "") -> str:
    node_ids = [n["node_id"] for n in cfg.get("nodes", [])]
    options = "".join(f'<option value="{esc(i)}">{esc(i)}</option>'
                      for i in node_ids)
    rows = []
    for n in cfg.get("nodes", []):
        kind = n.get("type", "?")
        conns = []
        for target in n.get("next_node_ids") or []:
            conns.append(
                f'<span class="badge">{esc(kind[:1])}→{esc(target)}</span> '
                f'<form method="post" action="/flow/connections/remove" '
                f'class="row" style="display:inline">'
                f'<input type="hidden" name="source" value="{esc(n["node_id"])}">'
                f'<input type="hidden" name="destination" '
                f'value="{esc(target)}"><button>×</button></form>'
            )
        fb = n.get("fallback_next")
        fb_html = (f'<span class="badge">fallback→{esc(fb)}</span>'
                   if fb else "")
        if kind == "verify":
            conns = [f'<span class="badge">match→{esc(n["match_next"])}</span>',
                     f'<span class="badge">mismatch→{esc(n["mismatch_next"])}</span>']
        rows.append(
            f'<tr><td class="mono"><strong>{esc(n["node_id"])}</strong>'
            f'{" <span class=badge>start</span>" if n["node_id"] == cfg.get("start_node_id") else ""}</td>'
            f"<td>{esc(kind)}</td><td>{' '.join(conns)} {fb_html}</td></tr>"
        )
    diagram = flow_svg(cfg)
    # Phase 6 guardrail score in the flow header (warnings are non-blocking).
    try:
        from platform.flow.models import Flow as _Flow
        from platform.flow.validator import validate as _validate

        _score = _validate(_Flow.model_validate(cfg)).guardrail_score
    except Exception:
        _score = 0
    score_html = (
        f'<span class="badge" style="color:var(--warn);'
        f'border-color:var(--warn)">guardrail score: {_score}</span>')
    return page(
        "Flow editor",
        f"""
<h1>Flow editor</h1>
{f'<p class="ok">{esc(message)}</p>' if message else ''}
<div class="row">
  <button hx-post="/flow/validate" hx-target="#validation"
          hx-swap="innerHTML">Validate flow</button>
  {score_html}
  <span class="dim">Saved config: configs/finance_employee.json</span>
</div>
{validation_panel(errors)}
<div class="mode-edit">
<h2>Draft with AI</h2>
<p class="dim">Describe the employee you want; the model drafts a flow
config. You see a diff and a preview first - it is applied only when you
click Apply, and it can never enable a tool your role does not hold.</p>
<form method="post" action="/flow/draft" class="row"
      hx-post="/flow/draft" hx-target="#draft-result" hx-swap="innerHTML">
  <input name="description" style="flex:3" required
         placeholder="plain-English description of the employee">
  <button class="primary">Draft</button>
</form>
<div id="draft-result"></div>
</div>
<h2>Connections</h2>
<form method="post" action="/flow/connections" class="row">
  <select name="source">{options}</select>
  <span class="dim">→</span>
  <select name="destination">{options}</select>
  <button class="primary">Add connection</button>
</form>
<table>
<tr><th>Node</th><th>Type</th><th>Outgoing connections</th></tr>
{"".join(rows)}
</table>
<h2>Diagram</h2>
<p class="dim">Click a node to open its edit form in the panel beside the
diagram; saving refreshes this diagram. Connections still use the form above.</p>
<div class="flow-layout">
  <div class="panel" id="diagram" hx-get="/flow/diagram"
       hx-trigger="refresh-diagram from:body" hx-swap="innerHTML">{diagram}</div>
  <div id="node-panel" class="panel">
    <span class="dim">Click a node in the diagram to edit it.</span>
  </div>
</div>
<script>
async function runNodeTest(nodeId) {{
  const out = document.getElementById("test-output");
  const sampleEl = document.getElementById("test-sample");
  if (!out) return;
  out.textContent = "";
  const body = new URLSearchParams({{sample: sampleEl ? sampleEl.value : ""}});
  const resp = await fetch("/flow/nodes/" + nodeId + "/test",
                           {{method: "POST", body: body}});
  if (!resp.body) {{ out.textContent = await resp.text(); return; }}
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {{
    const {{done, value}} = await reader.read();
    if (done) break;
    buf += dec.decode(value, {{stream: true}});
    let i;
    while ((i = buf.indexOf("\\n\\n")) >= 0) {{
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (chunk.startsWith("data: ")) {{
        try {{
          const d = JSON.parse(chunk.slice(6));
          if (d.text) out.textContent += d.text;
        }} catch (e) {{}}
      }}
    }}
  }}
}}
</script>""",
        active="flow", mode=mode,
    )


def agents_page(cfg: dict, message: str = "", mode: str = "use") -> str:
    all_tools = _registry_names()
    cards = []
    for n in cfg.get("nodes", []):
        if n.get("type") != "agent":
            continue
        node_id = n["node_id"]
        fallback = n.get("fallback_next") or ""
        targets = "".join(
            f'<option value="{esc(i)}" '
            f'{"selected" if i == fallback else ""}>{esc(i)}</option>'
            for i in [t["node_id"] for t in cfg.get("nodes", [])]
        )
        badges = "".join(
            tool_badge(node_id, t, t in (n.get("tools_enabled") or []))
            for t in all_tools
        )
        cards.append(f"""
<div class="panel">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(node_id)}</h2>
    <span class="dim mono">save_as: {esc(n.get("save_as") or "—")}</span>
  </div>
  <form method="post" action="/agents/{esc(node_id)}">
    <p><label class="dim">System prompt (file path or inline text)</label>
    <textarea name="system_prompt" rows="4" style="width:100%"
      >{esc(n.get("system_prompt") or "")}</textarea></p>
    <p><label class="dim">Instructions</label>
    <textarea name="instructions" rows="5" style="width:100%"
      >{esc(n.get("instructions") or "")}</textarea></p>
    <div class="row">
      <label class="dim">Save answer as
        <input name="save_as" value="{esc(n.get("save_as") or "")}"
               placeholder="session variable name"></label>
      <label class="dim">Fallback next
        <select name="fallback_next">{targets}</select></label>
      <button class="primary">Save agent</button>
    </div>
  </form>
  <p class="row">{badges}</p>
</div>""")
    node_ids = [n["node_id"] for n in cfg.get("nodes", [])]
    targets = "".join(f'<option value="{esc(i)}">{esc(i)}</option>'
                      for i in node_ids)
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    return page(
        "Agents",
        f"""
<h1>Agents</h1>
{msg}
{"".join(cards) or '<p class="dim">No agent nodes in this flow.</p>'}
<h2>New agent node</h2>
<form method="post" action="/agents" class="row">
  <input name="node_id" placeholder="node id (e.g. review_agent)" required
         pattern="[A-Za-z0-9_]+">
  <select name="next_node_ids">{targets}</select>
  <select name="fallback_next"><option value="">no fallback</option>
    {targets}</select>
  <button class="primary">Create agent</button>
</form>
<p class="dim">New nodes join the flow from the selected next node; edits
and tool toggles apply from the next run.</p>""",
        active="agents", mode=mode,
    )


def messages_page(cfg: dict, message: str = "", mode: str = "use") -> str:
    var_names: list[str] = []
    for n in cfg.get("nodes", []):
        if n.get("type") == "agent" and n.get("save_as"):
            var_names.append(str(n["save_as"]))
    var_options = "".join(f'<option value="{esc(v)}">{esc(v)}</option>'
                          for v in var_names)
    node_ids = [n["node_id"] for n in cfg.get("nodes", [])]
    forms = []
    for n in cfg.get("nodes", []):
        if n.get("type") != "message":
            continue
        node_id = n["node_id"]
        opts = "".join(
            f'<option value="{esc(i)}" '
            f'{"selected" if i in (n.get("next_node_ids") or []) else ""}>'
            f"{esc(i)}</option>"
            for i in node_ids
        )
        fb = n.get("fallback_next") or ""
        fb_opts = "".join(
            f'<option value="{esc(i)}" {"selected" if i == fb else ""}>'
            f"{esc(i)}</option>" for i in node_ids
        )
        forms.append(f"""
<div class="panel">
  <h2 style="margin-top:0">{esc(node_id)}</h2>
  <form method="post" action="/messages/{esc(node_id)}">
    <textarea name="template" rows="3" style="width:100%"
              id="tpl-{esc(node_id)}">{esc(n.get("template") or "")}</textarea>
    <div class="row" style="margin-top:8px">
      <label class="dim">insert variable
        <select class="var-insert" data-target="tpl-{esc(node_id)}">
          <option value="">choose…</option>{var_options}
        </select></label>
      <label class="dim">next <select name="next_node_id">{opts}</select></label>
      <label class="dim">fallback when unset
        <select name="fallback_next">{fb_opts}</select></label>
      <button class="primary">Save message</button>
    </div>
  </form>
</div>""")
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    new_opts = "".join(f'<option value="{esc(i)}">{esc(i)}</option>'
                       for i in node_ids)
    create = f"""
<h2>New message node</h2>
<form method="post" action="/messages" class="row">
  <input name="node_id" placeholder="node id (e.g. ask_amount)" required
         pattern="[A-Za-z0-9_]+">
  <input name="template" placeholder="template, e.g. Amount for {{{{vars.x}}}}?"
         style="min-width:280px" required>
  <select name="next_node_id">{new_opts}</select>
  <select name="fallback_next"><option value="">no fallback</option>
    {new_opts}</select>
  <button class="primary">Create message node</button>
</form>
<p class="dim">Next may be the node itself (self-loop); visit limits stop
runaway loops.</p>"""
    script = """<script>
  document.querySelectorAll(".var-insert").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (!sel.value) return;
      var ta = document.getElementById(sel.dataset.target);
      var ins = "{{vars." + sel.value + "}}";
      var pos = ta.selectionStart || ta.value.length;
      ta.value = ta.value.slice(0, pos) + ins + ta.value.slice(pos);
      ta.focus();
      ta.selectionStart = ta.selectionEnd = pos + ins.length;
      sel.value = "";
    });
  });
</script>"""
    return page(
        "Message nodes",
        f"""
<h1>Message nodes</h1>
{msg}
{"".join(forms) or '<p class="dim">No message nodes in this flow.</p>'}
{create}
{script}""",
        active="messages", mode=mode,
    )


def documents_page(tenant: str, tenants: list[str], files: list[str],
                   agents: list[str], attachments: dict[str, list[str]],
                   error: str = "", mode: str = "use") -> str:
    tenant_opts = "".join(
        f'<option value="{esc(t)}" {"selected" if t == tenant else ""}>'
        f"{esc(t)}</option>" for t in tenants
    )
    rows = []
    for f in files:
        boxes = "".join(
            f'<label class="dim" style="margin-right:10px">'
            f'<input type="checkbox" name="agents" value="{esc(a)}" '
            f'{"checked" if f in attachments.get(a, []) else ""}>'
            f"{esc(a)}</label>"
            for a in agents
        )
        rows.append(
            f'<tr><td class="mono">{esc(f)}</td>'
            f'<td><form method="post" action="/documents/attach" class="row">'
            f'<input type="hidden" name="tenant" value="{esc(tenant)}">'
            f'<input type="hidden" name="filename" value="{esc(f)}">'
            f"{boxes}<button>Save attachments</button></form></td></tr>"
        ) or ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Documents",
        f"""
<h1>Documents</h1>
{err}
<form method="post" action="/documents/upload" enctype="multipart/form-data"
      class="row">
  <select name="tenant">{tenant_opts}</select>
  <input type="file" name="file" accept=".txt,.md,.csv,.json,.pdf" required>
  <button class="primary">Upload</button>
  <span class="dim">stored under company_data/&lt;tenant&gt;/</span>
</form>
<h2>Files in company_data/{esc(tenant)}/</h2>
<table>
<tr><th>File</th><th>Attach to agents</th></tr>
{"".join(rows) or '<tr><td colspan="2" class="dim">No files yet.</td></tr>'}
</table>
<p class="dim">Attached documents are injected into the agent's prompt
(next run). PDFs are stored but currently injected only if they contain
utf-8 text.</p>""",
        active="documents", mode=mode,
    )


def _registry_names() -> list[str]:
    from ai_operator.tools import registry

    return registry.names()


def models_page(models: list, audit: dict[str, list[dict]] | None = None,
                error: str = "", message: str = "",
                mode: str = "use") -> str:
    """Phase 4: the data models page (configure mode).

    Models are the separately-configurable extraction contract: field names,
    types, required flags, descriptions and per-field write/read agent lists.
    The audit section shows the field-level write trail from the runs' traces.
    """
    audit = audit or {}
    cards = []
    for model in models:
        fields = model.get("fields") or []
        rows = []
        for f in fields:
            rows.append(
                f"<tr><td class=\"mono\"><strong>{esc(f['name'])}</strong></td>"
                f"<td>{esc(f['type'])}</td>"
                f"<td>{'required' if f.get('required') else 'optional'}</td>"
                f"<td>{esc(f.get('description') or '')}</td>"
                f"<td class=\"mono\">{esc(', '.join(f.get('write_agents') or []) or '—')}</td>"
                f"<td class=\"mono\">{esc(', '.join(f.get('read_agents') or []) or '—')}</td>"
                f'<td><form method="post" '
                f'action="/models/{esc(model["name"])}/fields/{esc(f["name"])}/'
                f'delete"><button>×</button></form></td></tr>'
            )
        fields_html = "".join(rows) or (
            '<tr><td colspan="7" class="dim">No fields yet.</td></tr>')
        writes = audit.get(model["name"]) or []
        audit_rows = "".join(
            f"<tr><td class=\"mono\">{esc(w['run_id'])}</td>"
            f"<td>{esc(w.get('node') or '?')}</td>"
            f"<td class=\"mono\">{esc(w.get('name') or '')}</td>"
            f"<td class=\"mono\">{esc(w.get('old'))}</td>"
            f"<td class=\"mono\">{esc(w.get('new'))}</td>"
            f"<td class=\"dim\">{esc(w.get('time') or '')}</td></tr>"
            for w in writes[:20]
        ) or '<tr><td colspan="6" class="dim">No traced field writes yet.</td></tr>'
        cards.append(f"""
<div class="panel" id="model-{esc(model['name'])}">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(model['name'])}</h2>
    <div class="row">
      <span class="dim">{esc(model.get('description') or '')}</span>
      <form method="post" action="/models/{esc(model['name'])}/delete"
            class="row" style="display:inline"
            onsubmit="return confirm('Delete model {esc(model['name'])}?')">
        <button>Delete model</button></form>
    </div>
  </div>
  <table>
  <tr><th>Field</th><th>Type</th><th>Required</th><th>Description
  </th><th>Can write</th><th>Can read</th><th></th></tr>
  {fields_html}
  </table>
  <form method="post" action="/models/{esc(model['name'])}/fields"
        class="row">
    <input name="field_name" placeholder="field name" required
           pattern="[A-Za-z0-9_]+">
    <select name="field_type">
      <option value="text">text</option><option value="number">number</option>
      <option value="date">date</option><option value="boolean">boolean</option>
    </select>
    <label class="dim"><input type="checkbox" name="required" value="1">
      required</label>
    <input name="description" placeholder="what to extract" style="flex:2">
    <input name="write_agents" placeholder="write agents, comma separated"
           value="ap_agent">
    <input name="read_agents" placeholder="read agents, comma separated">
    <button class="primary">Add field</button>
  </form>
  <h3 class="dim" style="margin-top:18px">Field write audit</h3>
  <table>
  <tr><th>Run</th><th>Agent</th><th>Field</th><th>Old</th><th>New</th>
  <th>Time</th></tr>
  {audit_rows}
  </table>
</div>""")
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Data models",
        f"""
<h1>Data models</h1>
{msg}{err}
<p class="dim">A data model is the extraction contract an agent follows: field
names become session variable names, and the compiler appends this field list
to the agent's context. Field write/read permissions are enforced in code on
the variable write path.</p>
{"".join(cards) or '<p class="dim">No data models yet.</p>'}
<h2>New data model</h2>
<form method="post" action="/models" class="row">
  <input name="name" placeholder="model name (e.g. Vendor)" required
         pattern="[A-Za-z0-9_]+">
  <input name="description" placeholder="description" style="flex:2">
  <button class="primary">Create model</button>
</form>""",
        active="models", mode=mode,
    )


def connectors_page(connectors: list[dict], error: str = "",
                    message: str = "", mode: str = "use",
                    sources: list[dict] | None = None,
                    queries: list[dict] | None = None) -> str:
    """Phase 4: the connector registry page.

    A connector is a base URL configured once; its endpoints become tools in
    the registry (toggleable per agent on the Agents page) and are projected
    into the flow config so the next run registers them.
    Phase 6 section 9: also hosts read-only data sources (CSV/SQLite) and
    their saved queries, each saved query registered as one read tool.
    """
    methods = ("GET", "POST", "PUT", "PATCH", "DELETE")
    levels = ("read", "reversible_write", "irreversible_write")
    cards = []
    for conn in connectors:
        cid = conn["id"]
        rows = []
        for ep in conn.get("endpoints") or []:
            rows.append(
                f'<tr><td class="mono"><strong>{esc(ep.get("name"))}</strong>'
                f'</td><td class="mono">{esc(ep.get("method") or "GET")}</td>'
                f'<td class="mono">{esc(ep.get("path") or "")}</td>'
                f"<td>{esc(ep.get('level') or 'read')}</td>"
                f"<td>{esc(ep.get('description') or '')}</td>"
                f'<td class="dim">{esc(ep.get("response_fields") or "")}</td>'
                f'<td><form method="post" action="/connectors/{cid}/'
                f'endpoints/{esc(ep.get("name"))}/delete"><button>×</button>'
                f"</form></td></tr>"
            ) or ('<tr><td colspan="7" class="dim">No endpoints yet.</td>'
                  "</tr>")
        method_opts = "".join(
            f'<option value="{m}">{m}</option>' for m in methods)
        level_opts = "".join(
            f'<option value="{lv}">{lv}</option>' for lv in levels)
        cards.append(f"""
<div class="panel" id="connector-{cid}">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(conn["name"])}</h2>
    <div class="row">
      <span class="dim mono">{esc(conn["base_url"])}</span>
      <form method="post" action="/connectors/{cid}/delete"
            class="row" style="display:inline"
            onsubmit="return confirm('Delete connector {esc(conn["name"])}?')">
        <button>Delete connector</button></form>
    </div>
  </div>
  <table>
  <tr><th>Tool name</th><th>Method</th><th>Path</th><th>Permission</th>
  <th>Description</th><th>Response fields</th><th></th></tr>
  {"".join(rows)}
  </table>
  <form method="post" action="/connectors/{cid}/endpoints" class="row">
    <input name="name" placeholder="tool name" required pattern="[A-Za-z0-9_]+">
    <select name="method">{method_opts}</select>
    <input name="path" placeholder="/api/path?tenant={{{{session.tenant}}}}"
           required style="flex:2">
    <select name="level">{level_opts}</select>
    <input name="description" placeholder="what it does" style="flex:2">
    <input name="response_fields" placeholder="response fields it reads">
    <button class="primary">Add endpoint</button>
  </form>
</div>""")
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Connectors",
        f"""
<h1>Connectors</h1>
{msg}{err}
<p class="dim">A connector is an external API configured once by base URL;
each endpoint becomes a tool in the registry - it shows up as a toggle on the
Agents page and is available to agents from the next run.</p>
{"".join(cards) or '<p class="dim">No connectors yet.</p>'}
<h2>New connector</h2>
<form method="post" action="/connectors" class="row">
  <input name="name" placeholder="name (e.g. ERP System)" required
         pattern="[A-Za-z0-9_]+">
  <input name="base_url" placeholder="base URL" value="{{{{app.base_url}}}}"
         required style="flex:2">
  <input name="headers" placeholder='default headers JSON' value="&#123;&#125;">
  <button class="primary">Create connector</button>
</form>
{_data_source_sections(sources or [], queries or [])}""",
        active="connectors", mode=mode,
    )


def _data_source_sections(sources: list[dict], queries: list[dict]) -> str:
    """Phase 6 section 9: read-only data sources + saved queries."""
    src_rows = ""
    for s in sources:
        src_rows += (
            f'<tr><td class="mono">{s["id"]}</td>'
            f'<td><strong>{esc(s["name"])}</strong></td>'
            f'<td><span class="badge">{esc(s["type"])}</span></td>'
            f'<td class="mono">{esc(s["path"])}</td>'
            f'<td><form method="post" '
            f'action="/connectors/sources/{s["id"]}/delete" '
            f'onsubmit="return confirm(\'Delete source and its queries?\')">'
            f'<button>Delete</button></form></td></tr>')
    if not src_rows:
        src_rows = ('<tr><td colspan="5" class="dim">No data sources '
                    'yet.</td></tr>')
    q_rows = ""
    for q in queries:
        q_rows += (
            f'<tr><td class="mono">{q["id"]}</td>'
            f'<td class="mono"><strong>{esc(q["name"])}</strong></td>'
            f'<td>{esc(q.get("source_name") or "-")} '
            f'<span class="badge">{esc(q.get("source_type") or "")}</span></td>'
            f'<td class="mono" style="white-space:pre-wrap">'
            f'{esc((q.get("sql") or "")[:160])}</td>'
            f'<td><form method="post" '
            f'action="/connectors/queries/{q["id"]}/delete">'
            f'<button>Delete</button></form></td></tr>')
    if not q_rows:
        q_rows = ('<tr><td colspan="5" class="dim">No saved queries '
                  'yet.</td></tr>')
    opts = "".join(
        f'<option value="{s["id"]}">{esc(s["name"])}</option>'
        for s in sources)
    return f"""
<h2>Data sources (read-only)</h2>
<p class="dim">A CSV or SQLite file with named queries. Each saved query
becomes one <strong>read</strong> tool in the registry (same role checks as
every tool). The agent never supplies SQL: parameters come from
<code>{{{{vars.x}}}}</code> placeholders, bound as values, and sources open
read-only.</p>
<table>
<tr><th>ID</th><th>Name</th><th>Type</th><th>Path</th><th></th></tr>
{src_rows}
</table>
<form method="post" action="/connectors/sources" class="row">
  <input name="name" placeholder="name" required pattern="[A-Za-z0-9_]+">
  <select name="type"><option value="csv">csv</option>
    <option value="sqlite">sqlite</option></select>
  <input name="path" placeholder="path to file (relative to repo root)"
         required style="flex:3">
  <button class="primary">Add data source</button>
</form>
<h2>Saved queries</h2>
<table>
<tr><th>ID</th><th>Tool name</th><th>Source</th><th>SQL</th><th></th></tr>
{q_rows}
</table>
<form method="post" action="/connectors/queries" class="row">
  <select name="source_id">{opts}</select>
  <input name="name" placeholder="tool name" required
         pattern="[A-Za-z0-9_]+">
  <input name="sql"
         placeholder="SELECT * FROM data WHERE vendor = {{{{vars.vendor}}}}"
         required style="flex:3">
  <button class="primary">Add saved query</button>
</form>"""


def schedules_page(schedules: list[dict], sessions: list[dict],
                   error: str = "", message: str = "",
                   mode: str = "use") -> str:
    """Phase 5: scheduled background runs.

    cron is the tiny format 'daily HH:MM' or 'weekly mon HH:MM', evaluated at
    the schedule's timezone offset from UTC.
    """
    by_id = {s["id"]: s for s in sessions}
    rows = []
    for s in schedules:
        sess = by_id.get(s["session_id"])
        sess_txt = (f'#{s["session_id"]} {esc(sess["tenant"])}'
                    if sess else f'#{s["session_id"]}')
        enabled = bool(s.get("enabled"))
        toggle_label = "Disable" if enabled else "Enable"
        last = (time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(s["last_run_at"]))
                if s.get("last_run_at") else "-")
        rows.append(
            f'<tr><td class="mono">{esc(s["cron"])}</td>'
            f'<td>{sess_txt}</td><td>{esc(s["task"])}</td>'
            f'<td><span class="badge {"completed" if enabled else "interrupted"}">'
            f'{"enabled" if enabled else "disabled"}</span></td>'
            f'<td class="dim">{esc(last)}</td>'
            f'<td class="mono">{esc(s.get("last_run_id") or "-")}</td>'
            f'<td class="row">'
            f'<form method="post" action="/schedules/{s["id"]}/toggle">'
            f'<button>{toggle_label}</button></form>'
            f'<form method="post" action="/schedules/{s["id"]}/delete" '
            f'onsubmit="return confirm(\'Delete schedule {s["id"]}?\')">'
            f'<button>Delete</button></form></td></tr>'
        )
    if not rows:
        rows = ('<tr><td colspan="7" class="dim">No schedules yet.</td></tr>')
    session_opts = "".join(
        f'<option value="{s["id"]}">#{s["id"]} {esc(s["tenant"])} '
        f'({esc(s["currency"])})</option>' for s in sessions)
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Schedules",
        f"""
<h1>Scheduled runs</h1>
{msg}{err}
<p class="dim">A background thread checks every 60 seconds. A schedule fires
when the clock reaches the configured time (within the same minute) and has
not already run in that window; if a run is already active it is recorded as
a skipped run and retried at the next check.</p>
<table>
<tr><th>When</th><th>Session</th><th>Task</th><th>Status</th>
<th>Last run</th><th>Last run id</th><th></th></tr>
{"".join(rows)}
</table>
<h2>New schedule</h2>
<form method="post" action="/schedules" class="row">
  <select name="session_id">{session_opts}</select>
  <input name="task" placeholder="task to run" required style="flex:2">
  <input name="cron" placeholder="daily 09:30   or   weekly mon 09:30"
         required>
  <input name="tz_offset" type="number" step="0.5" value="5.5" style="width:90px"
         title="timezone offset from UTC in hours">
  <button class="primary">Add schedule</button>
</form>""",
        active="schedules", mode=mode,
    )


def triggers_page(triggers: list[dict], sessions: list[dict],
                  error: str = "", message: str = "",
                  mode: str = "use") -> str:
    """Phase 6 section 5: event triggers (inbox folder + webhook)."""
    by_id = {s["id"]: s for s in sessions}
    rows = ""
    for t in triggers:
        sess = by_id.get(t["session_id"])
        sess_txt = (f'#{t["session_id"]} {esc(sess["tenant"])}'
                    if sess else f'#{t["session_id"]}')
        enabled = bool(t.get("enabled"))
        cfg = t.get("config") or {}
        detail = (esc(cfg.get("path") or "company_data/<tenant>/inbox")
                  if t["type"] == "inbox_folder" else
                  "header X-Trigger-Secret: " +
                  esc((str(t.get("secret") or ""))[:4] + "…"
                      if t.get("secret") else "(none)"))
        last = (time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(t["last_fired_at"]))
                if t.get("last_fired_at") else "-")
        run_link = (f'<a class="mono" href="/runs/{esc(t["last_run_id"])}">'
                    f'{esc(str(t["last_run_id"])[:24])}</a>'
                    if t.get("last_run_id") else "-")
        rows += (
            f'<tr><td class="mono">{t["id"]}</td>'
            f'<td><span class="badge">{esc(t["type"])}</span></td>'
            f'<td>{sess_txt}</td>'
            f'<td class="mono">{detail}</td>'
            f'<td class="mono" title="{esc(t.get("task_template") or "")}">'
            f'{esc((t.get("task_template") or "")[:60])}</td>'
            f'<td><span class="badge '
            f'{"completed" if enabled else "interrupted"}">'
            f'{"enabled" if enabled else "disabled"}</span></td>'
            f'<td class="dim">{esc(last)}</td><td>{run_link}</td>'
            f'<td class="row">'
            f'<form method="post" action="/triggers/{t["id"]}/toggle">'
            f'<button>{"Disable" if enabled else "Enable"}</button></form>'
            f'<form method="post" action="/triggers/{t["id"]}/delete" '
            f'onsubmit="return confirm(\'Delete trigger {t["id"]}?\')">'
            f'<button>Delete</button></form></td></tr>')
    if not rows:
        rows = ('<tr><td colspan="9" class="dim">No triggers yet.</td></tr>')
    session_opts = "".join(
        f'<option value="{s["id"]}">#{s["id"]} {esc(s["tenant"])}'
        f'</option>' for s in sessions)
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Triggers",
        f"""
<h1>Event triggers</h1>
{msg}{err}
<p class="dim">A trigger starts a run through the same path as the console
(one run at a time; skips are recorded with a reason). <strong>inbox_folder</strong>:
the scheduler polls the folder every 60s, runs <code>{{{{file.name}}}}</code>
templates and moves files to <code>processed/</code> (or <code>failed/</code>
when the run fails). <strong>webhook</strong>: POST
<code>/api/v1/triggers/&lt;id&gt;/fire</code> with the secret in the
<code>X-Trigger-Secret</code> header; the JSON body fills template values.</p>
<table>
<tr><th>ID</th><th>Type</th><th>Session</th><th>Path / secret</th>
<th>Task template</th><th>Status</th><th>Last fired</th><th>Last run</th>
<th></th></tr>
{rows}
</table>
<h2>New trigger</h2>
<form method="post" action="/triggers" class="row">
  <select name="type">
    <option value="inbox_folder">inbox_folder</option>
    <option value="webhook">webhook</option>
  </select>
  <select name="session_id">{session_opts}</select>
  <input name="path" placeholder="inbox folder path (inbox triggers)"
         style="flex:2">
  <input name="task_template" required
         placeholder="task: process {{{{file.name}}}} or ping {{{{note}}}}"
         style="flex:3">
  <input name="secret" placeholder="webhook secret (blank = generate)"
         style="flex:2">
  <button class="primary">Add trigger</button>
</form>""",
        active="triggers", mode=mode,
    )


def channels_page(channels: list[dict], notifications: list[dict],
                  error: str = "", message: str = "",
                  mode: str = "use") -> str:
    """Phase 6 section 6: approval and alert channels + delivery log."""
    rows = ""
    for c in channels:
        cfg = c.get("config") or {}
        if c["type"] == "webhook":
            detail = esc(cfg.get("url") or "(no url)")
        else:
            detail = (f'{esc(cfg.get("host") or "(no host)")}:{esc(cfg.get("port") or 587)}'
                      f' &rarr; {esc(cfg.get("to") or "(no to)")}')
        enabled = bool(c.get("enabled"))
        rows += (
            f'<tr><td class="mono">{c["id"]}</td><td>{esc(c["name"])}</td>'
            f'<td><span class="badge">{esc(c["type"])}</span></td>'
            f'<td class="mono">{detail}</td>'
            f'<td><span class="badge '
            f'{"completed" if enabled else "interrupted"}">'
            f'{"enabled" if enabled else "disabled"}</span></td>'
            f'<td class="row">'
            f'<form method="post" action="/channels/{c["id"]}/test">'
            f'<button>Send test</button></form>'
            f'<form method="post" action="/channels/{c["id"]}/toggle">'
            f'<button>{"Disable" if enabled else "Enable"}</button></form>'
            f'<form method="post" action="/channels/{c["id"]}/delete" '
            f'onsubmit="return confirm(\'Delete channel {c["id"]}?\')">'
            f'<button>Delete</button></form></td></tr>')
    if not rows:
        rows = ('<tr><td colspan="5" class="dim">No channels yet. '
                'Add a webhook or SMTP channel below.</td></tr>')
    notif_rows = ""
    for n in notifications[:20]:
        when = (time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(n["created_at"]))
                if n.get("created_at") else "-")
        status = str(n.get("status") or "-")
        link_cell = (f'<a class="mono" href="{esc(n["link"])}">open</a>'
                     if n.get("link") and str(n["link"]).startswith("http")
                     else '<span class="dim">-</span>')
        notif_rows += (
            f'<tr><td class="dim">{esc(when)}</td>'
            f'<td>{esc(n.get("channel_name") or "-")} '
            f'<span class="badge">{esc(n.get("channel_type") or "")}</span></td>'
            f'<td><span class="badge">{esc(n["kind"])}</span></td>'
            f'<td class="mono"><a href="/runs/{esc(n["run_id"])}">'
            f'{esc(str(n["run_id"])[:24])}</a></td>'
            f'<td class="mono" title="{esc(n.get("error") or "")}">'
            f'{esc(str(n.get("summary") or "")[:70])}</td>'
            f'<td><span class="badge '
            f'{"completed" if status == "sent" else "interrupted"}">'
            f'{esc(status)}</span></td><td>{link_cell}</td></tr>')
    if not notif_rows:
        notif_rows = ('<tr><td colspan="7" class="dim">No notifications '
                      'sent yet.</td></tr>')
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Channels",
        f"""
<h1>Approval &amp; alert channels</h1>
{msg}{err}
<p class="dim">When a run enters <strong>waiting_approval</strong> each
enabled channel gets the action, the policy rule and a signed one-time link
to the approval card (single use; expired links are rejected). Runs ending
<strong>failed</strong> or <strong>interrupted</strong> also alert. The link
opens the card only &mdash; approving still enforces the approver's role
limit.</p>
<table>
<tr><th>ID</th><th>Name</th><th>Type</th><th>Destination</th>
<th>Status</th><th></th></tr>
{rows}
</table>
<h2>New channel</h2>
<form method="post" action="/channels" class="row">
  <input name="name" placeholder="name" style="flex:1" required>
  <select name="type">
    <option value="webhook">webhook (Slack/Teams)</option>
    <option value="email_smtp">email_smtp</option>
  </select>
  <input name="config" style="flex:3" required
         placeholder='config JSON: {{"url": "https://hooks..."}} or '
                    '{{"host": "smtp...", "to": "..."}}'>
  <label class="dim"><input type="checkbox" name="enabled" checked>
  enabled</label>
  <button class="primary">Add channel</button>
</form>
<h2>Recent notifications</h2>
<table>
<tr><th>When</th><th>Channel</th><th>Kind</th><th>Run</th><th>Summary</th>
<th>Status</th><th>Link</th></tr>
{notif_rows}
</table>""",
        active="channels", mode=mode,
    )


def skills_page(skills: list[dict], users: dict[str, list[str]],
                error: str = "", message: str = "", mode: str = "use") -> str:
    """Phase 5: reusable skills and which agents currently attach each one."""
    rows = ""
    for s in skills:
        name = str(s["name"])
        used_by = users.get(name) or []
        used = ", ".join(used_by) if used_by else "—"
        body = (s.get("body") or "")
        preview = esc(body[:300]) + ("…" if len(body) > 300 else "")
        rows += (
            f'<tr><td class="mono"><strong>{esc(name)}</strong></td>'
            f'<td>{esc(s.get("description") or "")}</td>'
            f'<td class="dim">{esc(used)}</td>'
            f'<td><details><summary class="dim">body</summary>'
            f'<pre>{preview}</pre></details></td>'
            f'<td><form method="post" action="/skills/{esc(name)}/delete" '
            f'onsubmit="return confirm(\'Delete skill {esc(name)}?\')">'
            f"<button>Delete</button></form></td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="5" class="dim">No skills yet.</td></tr>'
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Skills",
        f"""
<h1>Skills</h1>
<p class="dim">Reusable instruction blocks, global to the platform. Attach
them to an agent in the flow editor; the compiler appends each skill's body
below the agent's instructions and above its data model block.</p>
{msg}{err}
<table>
<tr><th>Name</th><th>Description</th><th>Used by</th><th>Body</th><th></th>
</tr>
{rows}
</table>
<h2>New skill</h2>
<form method="post" action="/skills" class="row">
  <input name="name" placeholder="skill name" required
         pattern="[A-Za-z0-9_-]+" style="flex:1">
  <input name="description" placeholder="one-line description" style="flex:2">
  <textarea name="body" rows="3" placeholder="instruction content"
            required style="flex:4"></textarea>
  <button class="primary">Add skill</button>
</form>""",
        active="skills", mode=mode,
    )


def apikeys_page(keys: list[dict], roles: list[dict], message: str = "",
                 error: str = "", fresh_key: str = "",
                 mode: str = "use") -> str:
    """Phase 6 section 2: API keys - create (shown once), list, revoke."""
    rows = ""
    for k in keys:
        created = (time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(k["created_at"]))
                   if k.get("created_at") else "-")
        revoked = bool(k.get("revoked"))
        status = (f'<span class="badge">revoked</span>' if revoked else
                  f'<span class="badge complete">active</span>')
        revoke_btn = (
            "" if revoked else
            f'<form method="post" action="/apikeys/{k["id"]}/revoke">'
            f'<button>Revoke</button></form>')
        rows += (
            f'<tr><td class="mono">{esc(k["id"])}</td>'
            f'<td>{esc(k["label"])}</td>'
            f'<td class="mono">{esc(k.get("role_name") or "-")}</td>'
            f'<td class="dim">{esc(created)}</td><td>{status}</td>'
            f"<td>{revoke_btn}</td></tr>")
    if not rows:
        rows = '<tr><td colspan="6" class="dim">No API keys yet.</td></tr>'
    role_opts = "".join(
        f'<option value="{r["id"]}">{esc(r["name"])}</option>'
        for r in roles)
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    fresh = ""
    if fresh_key:
        fresh = (
            f'<div class="panel" style="border-color:var(--warn)">'
            f'<p><strong>Copy this key now - it is shown only once:</strong></p>'
            f'<pre class="mono" style="white-space:pre-wrap">'
            f'{esc(fresh_key)}</pre>'
            f'<p class="dim">Send it as <code>Authorization: Bearer '
            f'&lt;key&gt;</code> or <code>X-API-Key: &lt;key&gt;</code>.</p>'
            f'</div>')
    return page(
        "API keys",
        f"""
<h1>API keys</h1>
{msg}{err}{fresh}
<p class="dim">Keys authenticate the versioned HTTP API
(<code>/api/v1/...</code>) and the MCP server. Each key runs as its role and
is subject to the same role checks as internal actors; only a SHA-256 hash is
stored.</p>
<table>
<tr><th>ID</th><th>Label</th><th>Role</th><th>Created</th><th>Status</th><th></th></tr>
{rows}
</table>
<h2>New key</h2>
<form method="post" action="/apikeys" class="row">
  <input name="label" placeholder="label (e.g. n8n bridge)" required
         style="flex:2">
  <select name="role_id">{role_opts}</select>
  <button class="primary">Create key</button>
</form>""",
        active="apikeys", mode=mode,
    )


def roles_page(roles: list[dict], permissions: list[dict], resources: list[str],
               cycles: dict[str, list[str]], message: str = "",
               error: str = "", mode: str = "use") -> str:
    """Phase 6: roles, and the permissions matrix (roles x resources).

    Every cell is an inline toggle - clicking cycles the access level for that
    role/resource through the levels allowed for that kind. Every change is
    written to the audit trail as role_permission_changed.
    """
    lookup: dict[tuple[str, str], str] = {}
    for p in permissions:
        lookup[(str(p["role_name"]), str(p["resource"]))] = str(p["access"])

    def cell(role: dict, resource: str) -> str:
        rname = str(role["name"])
        current = lookup.get((rname, resource), "none")
        kind = next((k for k in cycles if resource.startswith(k)), "tool:")
        _cycle = cycles[kind]
        nxt = _cycle[(_cycle.index(current) + 1) % len(_cycle)]
        cls = {"none": "", "read": "", "write": "btn-write",
               "approve": "btn-approve"}.get(current, "")
        return (f'<button class="{cls}" hx-post="/roles/{role["id"]}/permissions" '
                f'hx-vals=\'{{"resource": "{esc(resource)}"}}\' '
                f'title="{esc(kind + " " + ", ".join(_cycle))}">'
                f"{esc(current)} → {esc(nxt)}</button>")

    headers_parts = []
    for resource in resources:
        kind = next((k for k in cycles if resource.startswith(k)), "tool:")
        headers_parts.append(
            f"<th>{esc(resource)}<br>"
            f"<span class='dim'>{esc(', '.join(cycles.get(kind, [])))}</span></th>"
        )
    headers = "".join(headers_parts)
    rows = ""
    for role in roles:
        cells = "".join(
            f"<td>{cell(role, resource)}</td>" for resource in resources
        )
        rows += (
            f"<tr><td class='mono'><strong>{esc(role['name'])}</strong></td>"
            f"<td>{esc(role.get('description') or '')}</td>"
            f"<td class='dim'>{role['id']}</td>{cells}"
            f"<td><form method='post' action='/roles/{role['id']}/delete' "
            f"onsubmit=\"return confirm('Delete role {esc(role['name'])}? "
            f"It removes all its permissions.')\"><button>Delete</button>"
            f"</form></td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="5" class="dim">No roles yet.</td></tr>'
    options = "".join(
        f'<option value="{esc(r)}">{esc(r)}</option>'
        for r in _role_names()
    )
    msg = f'<p class="ok">{esc(message)}</p>' if message else ""
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return page(
        "Roles",
        f"""
<h1>Roles</h1>
<p class="dim">Roles are shared by humans and agents. An agent node's
<code>role</code> gates its tool calls and field writes; a session's user role
gates approvals (within the role's approve limit). <code>kind:*</code> rows act
as the fallback for a whole kind.</p>
{msg}{err}
<h2>Permissions matrix</h2>
<p class="dim">Click a cell to cycle its level; every change is written to the
audit trail.</p>
<div style="overflow-x:auto">
<table class="matrix">
<tr><th>Role</th><th>Description</th><th>ID</th>{headers}<th></th></tr>
{rows}
</table>
</div>
<h2>New role</h2>
<form method="post" action="/roles" class="row">
  <select name="role_template" disabled style="display:none">
    <option value="">{options}</option>
  </select>
  <input name="name" placeholder="role name" required
         pattern="[A-Za-z0-9_-]+" style="flex:2">
  <input name="description" placeholder="one-line description" style="flex:4">
  <button class="primary">Add role</button>
</form>
<p class="dim">Seeded roles: {esc(", ".join(_role_names())) or "none"}.
A new role starts with no rows; click tool/field/action cells to grant it
access, or leave it read-only by assignment.</p>""",
        active="roles", mode=mode,
    )


_AUDIT_LABELS = {
    "api_key_created": "l-write",
    "api_key_revoked": "l-err",
    "trigger_created": "l-write",
    "trigger_deleted": "l-write",
    "channel_created": "l-write",
    "channel_deleted": "l-write",
    "variable_written": "l-write",
    "permission_denied": "l-err",
    "permission_denied_role": "l-err",
    "approval_denied_role": "l-err",
    "tool_disabled": "l-err",
    "unknown_tool": "l-err",
    "visit_limit_hit": "l-err",
    "run_error": "l-err",
    "understand_failed": "l-err",
    "budget_exhausted": "l-err",
    "approval_requested": "l-warn",
    "human_input": "l-warn",
    "clarification": "l-warn",
    "verification": "l-info",
    "report_written": "l-info",
    "finish": "l-info",
    "role_created": "l-write",
    "role_deleted": "l-write",
    "role_permission_changed": "l-write",
}


def _approval_decisions(events: list[dict]) -> dict[int, str]:
    """Index of each approval_requested -> the answer that resolved it.

    The decision is a separate human_input event that follows the request in
    the same run; walk each run chronologically and attach the first approval
    answer to the most recent still-pending request.
    """
    by_run: dict[str, list[tuple[int, dict]]] = {}
    for i, ev in enumerate(events):  # events arrive reverse-chronological
        by_run.setdefault(str(ev.get("run_id")), []).append((i, ev))
    decisions: dict[int, str] = {}
    for items in by_run.values():
        pending: int | None = None
        for i, ev in reversed(items):
            kind = ev.get("event")
            if kind == "approval_requested":
                pending = i
            elif (kind == "human_input" and ev.get("kind") == "approval"
                    and pending is not None):
                decisions[pending] = str(ev.get("answer") or "decided")
                pending = None
    return decisions


def _audit_summary(ev: dict, decision: str | None = None) -> str:
    """The short human-readable summary for one trace event."""
    kind = ev.get("event")
    if kind == "variable_written":
        return (f"{ev.get('name')}: "
                f"{json.dumps(ev.get('old'), default=str)} → "
                f"{json.dumps(ev.get('new'), default=str)}")
    if kind == "permission_denied":
        return f"{ev.get('node')} denied writing '{ev.get('name')}'"
    if kind == "permission_denied_role":
        return (f"role {ev.get('role')} blocked tool "
                f"{ev.get('tool_name')}: {ev.get('reason')}")
    if kind == "approval_denied_role":
        return (f"role {ev.get('role')} denied approval"
                + (f" for {ev.get('amount')}" if ev.get("amount") is not None else "")
                + f": {ev.get('reason')}")
    if kind == "role_permission_changed":
        return (f"{ev.get('role')} · {ev.get('resource')}: "
                f"{ev.get('old')} → {ev.get('new')}")
    if kind == "role_created":
        return f"role {ev.get('role')} created"
    if kind == "role_deleted":
        return f"role {ev.get('role')} deleted"
    if kind == "api_key_created":
        return f"api key '{ev.get('label')}' created for role {ev.get('role')}"
    if kind == "api_key_revoked":
        return f"api key #{ev.get('key_id')} revoked"
    if kind == "tool_disabled":
        why = ("not enabled for this agent" if ev.get("scope") == "agent"
               else f"not enabled for session {ev.get('tenant')}")
        return f"{ev.get('tool_name')} blocked: {why}"
    if kind == "visit_limit_hit":
        return (f"node {ev.get('node')} hit visit limit {ev.get('limit')} "
                f"(total {ev.get('total')})")
    if kind == "approval_requested":
        base = (f"{ev.get('tool_name')} needs approval: {ev.get('reason')}")
        return f"{base} → {decision}" if decision else base
    if kind in ("verification", "verification_result"):
        verdict = "matched" if ev.get("matched") else "MISMATCH"
        detail = ev.get("details") or ""
        if not ev.get("matched"):
            detail += (f" expected={json.dumps(ev.get('expected'), default=str)}"
                       f" found={json.dumps(ev.get('found'), default=str)}")
        return f"{verdict}: {detail}"
    if kind == "human_input":
        return (f"{ev.get('kind') or 'input'}: "
                f"{ev.get('answer') or ev.get('resolved') or ''}")
    if kind == "decision":
        return f"{ev.get('action_type')}: {str(ev.get('thought') or '')[:90]}"
    if kind == "tool_result":
        return f"{ev.get('tool')} {'ok' if ev.get('ok') else 'FAILED'}"
    if kind == "message_rendered":
        return str(ev.get("rendered") or "")[:120]
    if kind in ("node_entered", "node_exited"):
        return f"{ev.get('node')} ({ev.get('node_type') or kind})"
    if kind == "report_written":
        return f"report status: {ev.get('status')}"
    if kind == "run_started":
        return str(ev.get("task") or "")[:120]
    if kind == "evidence_captured":
        return str(ev.get("path") or "")
    return ""


def audit_page(events: list[dict], run_id: str = "", node: str = "",
               event: str = "", mode: str = "use") -> str:
    """Phase 4: the audit trail - every trace event, newest first.

    Reads only trace.jsonl (no new data). Each row expands to the full
    event; run/node/event filters travel in the query string so a link can
    point at a specific filtered view.
    """
    decisions = _approval_decisions(events)
    rows = []
    for i, ev in enumerate(events):
        kind = str(ev.get("event") or "?")
        cls = _AUDIT_LABELS.get(kind, "l-dim")
        rid = str(ev.get("run_id") or "")
        summary = _audit_summary(ev, decisions.get(i))
        full = esc(json.dumps(ev, indent=2, default=str))
        rows.append(f"""
<details class="audit-row"><summary>
<span class="dim">{esc(ev.get("time") or "")}</span>
<a href="/runs/{esc(rid)}" class="mono">{esc(rid)}</a>
<span class="dim mono">{esc(ev.get("node") or "")}</span>
<b class="lbl {cls}">{esc(kind)}</b> <span>{summary}</span>
</summary><pre class="mono">{full}</pre></details>""")
    if not rows:
        rows.append('<p class="dim">No trace events match these filters.</p>')
    msg = ""
    return page(
        "Audit trail",
        f"""
<h1>Audit trail</h1>
{msg}
<p class="dim">Every event from every run's trace.jsonl, newest first.
Click a row to expand the full event.</p>
<form method="get" action="/audit" class="row">
  <input name="run_id" placeholder="filter by run id" value="{esc(run_id)}"
         style="flex:2">
  <input name="node" placeholder="filter by node" value="{esc(node)}">
  <input name="event" placeholder="filter by event type" value="{esc(event)}">
  <button>Apply filters</button>
  <a href="/audit"><button type="button">Clear</button></a>
</form>
<p class="dim">{len(events)} event(s)</p>
{''.join(rows)}""",
        active="audit", mode=mode,
    )
