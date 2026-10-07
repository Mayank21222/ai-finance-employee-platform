"""Server-rendered HTML for the dashboard: plain f-strings + htmx, no template lib."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

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


def flow_page(cfg: dict, errors: list[str] | None = None) -> str:
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
    diagram = esc(mermaid_diagram(cfg))
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
<pre class="mermaid" id="flowdiag">{diagram}</pre>
<script src="/static/mermaid.min.js"></script>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: "dark" }});
  document.body.addEventListener("htmx:afterSwap", function () {{
    mermaid.run({{ query: "#flowdiag" }});
  }});
</script>""",
        active="flow",
    )


def agents_page(cfg: dict, message: str = "") -> str:
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
        active="agents",
    )


def messages_page(cfg: dict, message: str = "") -> str:
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
        active="messages",
    )


def documents_page(tenant: str, tenants: list[str], files: list[str],
                   agents: list[str], attachments: dict[str, list[str]],
                   error: str = "") -> str:
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
        active="documents",
    )


def _registry_names() -> list[str]:
    from ai_operator.tools import registry

    return registry.names()
