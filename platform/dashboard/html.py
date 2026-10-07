"""Server-rendered HTML for the dashboard: plain f-strings + htmx, no template lib."""

from __future__ import annotations

import json
from html import escape

NAV = [
    ("runs", "/runs", "Run console"),
    ("sessions", "/sessions", "Sessions"),
    ("agents", "/agents", "Agents"),
    ("flow", "/flow", "Flow editor"),
    ("documents", "/documents", "Documents"),
    ("messages", "/messages", "Message nodes"),
]

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
  .badge.running { color:var(--warn); border-color:var(--warn); }
  .badge.complete, .badge.verified_complete, .badge.done { color:var(--ok);
       border-color:var(--ok); }
  .badge.failed, .badge.needs_human { color:var(--err); border-color:var(--err); }
  .trace { background:#0a0e13; border:1px solid var(--line); border-radius:6px;
           padding:10px; height:340px; overflow-y:auto; font-size:12.5px;
           font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .trace .ev { padding:2px 0; border-bottom:1px dashed #1a222c; }
  .trace .ev b { color:var(--acc); }
  .trace .ev.error b, .trace .ev.tool_error b { color:var(--err); }
  .badge-tool { cursor:pointer; }
  .badge-tool.on { color:var(--ok); border-color:var(--ok); }
  .badge-tool.off { color:var(--dim); opacity:.55; }
"""


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def page(title: str, body: str, active: str = "") -> str:
    nav = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">'
        f"{label}</a>"
        for key, href, label in NAV
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · Comp Ops</title>
<script src="/static/htmx.min.js"></script>
<style>{_CSS}</style>
</head><body>
<header>
  <span class="brand">COMP OPS</span>
  <nav>{nav}</nav>
</header>
<main>
{body}
</main>
</body></html>"""


def sessions_page(sessions: list[dict], error: str = "") -> str:
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
        active="sessions",
    )


def runs_page(runs: list[dict], sessions: list[dict]) -> str:
    rows = "".join(
        f'<tr><td class="mono"><a href="/runs/{esc(r["run_id"])}" '
        f'style="color:var(--acc)">{esc(r["run_id"])}</a></td>'
        f'<td>{esc(r["task"] or "")}</td><td>{esc(r["tenant"] or "-")}</td>'
        f'<td><span class="badge {esc(r["status"])}">{esc(r["status"])}</span></td>'
        f'<td class="dim">{r["created_at"]:.0f}</td></tr>'
        for r in runs
    ) or '<tr><td colspan="5" class="dim">No runs yet.</td></tr>'
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
<table>
<tr><th>Run</th><th>Task</th><th>Session</th><th>Status</th><th>Started</th></tr>
{rows}
</table>""",
        active="runs",
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


def run_detail_page(run_id: str, task: str, status: str, session: dict | None,
                    waiting: bool, question: str, report: dict | None,
                    error: str | None, agents_html: str, run_active: bool) -> str:
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
    if (ev.event === "approval_requested" ||
        ev.event === "human_input_requested") {{
      if (qEl) qEl.textContent = ev.question || JSON.stringify(ev);
      if (panel) panel.style.display = "";
    }}
    if (ev.event === "human_input") {{
      if (panel) panel.style.display = "none";
    }}
  }};
}})();
</script>"""
    body = f"""<div class="row" style="justify-content:space-between">
  <h1>Run <span class="mono">{esc(run_id)}</span></h1>
  <div class="row">
    <span class="badge {esc(status)}">{esc(status)}</span>
    {cancel}
    <a href="/runs"><button>Back</button></a>
  </div>
</div>
<p>{esc(task)}</p>
<p class="dim">session: {sess}</p>
{err_html}
{answer_form(run_id, question, waiting)}
<h2>live trace</h2>
<div class="trace" id="trace"></div>
<h2>agents</h2>
<div id="agents" {poll}>{agents_html}</div>
{report_html}
{script}"""
    return page(f"Run {run_id}", body, active="runs")


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
