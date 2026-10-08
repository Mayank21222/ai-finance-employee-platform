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
  <input name="user_role" placeholder="user role" value="finance_operator"
         required>
  <button class="primary">Add session</button>
</form>
<p class="dim">Runs are linked to a session by ID; the session supplies
tenant, currency, approval threshold and user role at start.</p>""",
        active="sessions", mode=mode,
    )


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
            f'<td>{esc(r["task"] or "")}</td>'
            f'<td>{esc(r["tenant"] or "-")} (#{r["session_id"] or "-"})</td>'
            f'<td class="dim">{esc(started)}</td>'
            f'<td class="dim">{esc(finished)}</td>'
            f'<td><a href="/runs/{esc(r["run_id"])}/trace">trace</a> · '
            f'<a href="/runs/{esc(r["run_id"])}/evidence">evidence</a></td></tr>'
        )
    if not rows:
        rows = '<tr><td colspan="7" class="dim">No runs yet.</td></tr>'
    return page(
        "History",
        """
<h1>Runs history</h1>
<p class="dim">State: queued, running, waiting_approval (interrupted at an
approval, resumable), completed, failed, interrupted (cut short by a restart
or cancel).</p>
<table>
<tr><th>Run</th><th>State</th><th>Task</th><th>Session</th><th>Started</th>
<th>Finished</th><th>Artifacts</th></tr>
""" + rows + "</table>",
        active="history", mode=mode,
    )


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
    return (f'<div class="approval-card pending">'
            f'<h2 style="margin-top:0">approval requested</h2>'
            f"{action}{policy}{summary_html}"
            f'<div class="row" style="margin-top:12px">'
            f'<button class="primary" hx-post="/runs/{esc(run_id)}/answer" '
            f'hx-vals=\'{{"answer": "approve"}}\' '
            f'hx-target="#approval-card-body" hx-swap="innerHTML">'
            f"Approve</button>"
            f'<button hx-post="/runs/{esc(run_id)}/answer" '
            f'hx-vals=\'{{"answer": "reject"}}\' '
            f'hx-target="#approval-card-body" hx-swap="innerHTML">'
            f"Reject</button>"
            f"</div></div>")


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
  const setState = function(s) {{
    if (stateEl) {{ stateEl.className = "badge " + s; stateEl.textContent = s; }}
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
<h2>live trace</h2>
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


def validation_panel(errors: list[str] | None = None) -> str:
    if errors is None:
        return ('<div class="panel" id="validation"><span class="dim">'
                "Click ‘Validate flow’ to check the config.</span></div>")
    if not errors:
        return ('<div class="panel" id="validation">'
                '<span class="ok">Flow is valid.</span></div>')
    lis = "".join(f"<li>{esc(e)}</li>" for e in errors)
    return ('<div class="panel" id="validation">'
            f'<span class="err">Flow has {len(errors)} problem(s):</span>'
            f"<ul>{lis}</ul></div>")


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
        fields = f"""
    <p><label class="dim">System prompt (file path or inline text)</label>
    <textarea name="system_prompt" rows="4" style="width:100%"
      >{esc(node.get("system_prompt") or "")}</textarea></p>
    <p><label class="dim">Instructions</label>
    <textarea name="instructions" rows="6" style="width:100%"
      >{esc(node.get("instructions") or "")}</textarea></p>
    <div class="row">
      <label class="dim">Save answer as
        <input name="save_as" value="{esc(node.get("save_as") or "")}"></label>
      <label class="dim">Fallback next
        <select name="fallback_next"
          >{options(str(node.get("fallback_next") or ""))}</select></label>
      <button class="primary">Save node</button>
    </div>"""
        extra = (f'<p class="dim">Attached documents: {esc(docs)}</p>'
                 f'<p class="row">{badges}</p>')
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
    return f"""
<div class="panel" id="node-panel">
  <div class="row" style="justify-content:space-between">
    <h2 style="margin:0">{esc(node_id)}</h2>
    <span class="badge">{esc(kind)}</span>
  </div>
  {msg}{err}
  {form_open}{fields}
  </form>
  {extra}
</div>"""


def flow_page(cfg: dict, errors: list[str] | None = None,
              mode: str = "use") -> str:
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
    return page(
        "Flow editor",
        f"""
<h1>Flow editor</h1>
<div class="row">
  <button hx-post="/flow/validate" hx-target="#validation"
          hx-swap="innerHTML">Validate flow</button>
  <span class="dim">Saved config: configs/finance_employee.json</span>
</div>
{validation_panel(errors)}
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
</div>""",
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
