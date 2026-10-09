"""Low-code platform dashboard.

Four screens: Flow / Agents / Run / History. One design system, HTML+CSS+SSE
with a small vanilla-JS layer (no external CDN, so the sandbox stays offline).
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager
from html import escape as _esc

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from ai_operator.models import Employee, FinalReport, FlowEdge, FlowNode, RunRecord
from ai_operator.observability import configure_logging, log_event, read_events
from ai_operator.runtime import Operator
from builder.flow import NODE_LIBRARY, ROLE_TEMPLATES, TOOL_LABELS, _slug, compile_employee, new_employee
from builder.store import Store

STORAGE_DIR = os.getenv("STORAGE_DIR", ".data")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8001")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
STORE_PATH = os.path.join(STORAGE_DIR, "builder.db")

store = Store(STORE_PATH)
RUNS: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# design system
# --------------------------------------------------------------------------- #
def css() -> str:
    return """
:root{--bg:#0f172a;--panel:#1e293b;--card:#fff;--ink:#0f172a;--muted:#64748b;
--line:#e2e8f0;--accent:#2563eb;--accent-ink:#1d4ed8;--ok:#16a34a;--warn:#d97706;
--bad:#dc2626;--radius:12px;--ring:0 0 0 3px rgba(37,99,235,.28)}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f8fafc;color:var(--ink)}
a{color:var(--accent);text-decoration:none}.app{display:flex;min-height:100vh}
.side{width:214px;background:var(--bg);color:#cbd5e1;padding:18px 14px;flex-shrink:0;display:flex;flex-direction:column}
.brand{font-weight:700;color:#fff;margin-bottom:18px;font-size:16px;display:flex;align-items:center;gap:8px}
.brand .dot{width:10px;height:10px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 4px rgba(22,163,74,.25)}
.nav{display:flex;flex-direction:column;gap:2px}
.side a{display:block;color:#cbd5e1;padding:9px 12px;border-radius:8px;margin:2px 0;transition:background .15s ease,color .15s ease,transform .12s ease,box-shadow .15s ease}
.side a:hover{background:#1e293b;color:#fff;transform:translateX(3px)}
.side a.active{background:var(--accent);color:#fff;box-shadow:0 6px 16px rgba(37,99,235,.35)}
.side-foot{margin-top:auto;padding-top:18px;font-size:12px;color:#94a3b8}
.main{flex:1;min-width:0}
.top{display:flex;justify-content:space-between;align-items:center;gap:16px;background:#fff;border-bottom:1px solid var(--line);padding:12px 22px;position:sticky;top:0;z-index:5}
.top-title{font-weight:600;color:var(--muted);display:flex;align-items:center;gap:9px;font-size:14px}
.subtitle{color:var(--muted);font-weight:500}
.toggle{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#f1f5f9}
.toggle a{padding:6px 16px;color:var(--muted);font-size:14px;transition:background .15s ease,color .15s ease}
.toggle a:hover{color:var(--ink)}
.toggle a.on{background:var(--accent);color:#fff;box-shadow:0 4px 10px rgba(37,99,235,.3)}
.wrap{padding:22px;max-width:1200px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px;transition:box-shadow .2s ease,transform .2s ease,border-color .2s ease}
.card:hover{box-shadow:0 10px 28px rgba(15,23,42,.09);border-color:#cbd5e1}
.card h3{margin:0 0 6px}.muted{color:var(--muted);font-size:13px}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;background:#eff6ff;color:var(--accent)}
.badge.ok{background:#dcfce7;color:var(--ok)}.badge.warn{background:#fef3c7;color:var(--warn)}.badge.bad{background:#fee2e2;color:var(--bad)}
.btn{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:9px;padding:9px 15px;cursor:pointer;font-size:14px;font-weight:600;line-height:1;text-align:center;
transition:transform .12s ease,box-shadow .18s ease,background .18s ease,opacity .18s ease;user-select:none;-webkit-tap-highlight-color:transparent}
.btn:hover{background:var(--accent-ink);transform:translateY(-1px);box-shadow:0 10px 20px rgba(37,99,235,.3)}
.btn:active{transform:translateY(0) scale(.97);box-shadow:0 2px 6px rgba(37,99,235,.25)}
.btn:focus-visible{outline:none;box-shadow:var(--ring)}
.btn.ghost{background:#fff;color:var(--accent);border:1px solid var(--accent)}
.btn.ghost:hover{background:#eff6ff;box-shadow:0 10px 20px rgba(37,99,235,.16)}
.btn.reject{background:#fff;color:var(--bad);border:1px solid var(--bad)}
.btn.reject:hover{background:#fef2f2;box-shadow:0 10px 20px rgba(220,38,38,.18)}
.btn.ok{background:var(--ok)}.btn.ok:hover{background:#15803d}
.btn[disabled],.btn.is-loading{opacity:.65;cursor:progress;transform:none!important;box-shadow:none!important;pointer-events:none}
.btn.is-loading::after{content:"";display:inline-block;width:12px;height:12px;margin-left:8px;vertical-align:-1px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite}
.btn.ghost.is-loading::after{border-color:rgba(37,99,235,.35);border-top-color:var(--accent)}
@keyframes spin{to{transform:rotate(360deg)}}
input,select,textarea{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;font:inherit;margin:4px 0;transition:border-color .15s ease,box-shadow .15s ease}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);box-shadow:var(--ring)}
label{font-size:13px;color:var(--muted);display:block;margin-top:10px}
.row{display:flex;gap:14px;flex-wrap:wrap}.row>*{flex:1;min-width:200px}
.canvas{position:relative;background:#fff;border:1px solid var(--line);border-radius:var(--radius);height:66vh;overflow:auto}
.node{position:absolute;width:180px;background:#fff;border:2px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.05);transition:border-color .15s ease,transform .12s ease,box-shadow .18s ease}
.node:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 10px 22px rgba(37,99,235,.16)}
.node.start{border-color:var(--ok)}.node.human{border-color:var(--warn)}
.slide{position:fixed;top:0;right:0;width:360px;height:100%;background:#fff;border-left:1px solid var(--line);padding:20px;box-shadow:-6px 0 24px rgba(0,0,0,.08);overflow:auto;animation:slidein .2s ease}
@keyframes slidein{from{transform:translateX(24px);opacity:0}to{transform:none;opacity:1}}
.timeline{list-style:none;padding:0;margin:0}.timeline li{padding:9px 12px;border-left:3px solid var(--line);margin:6px 0;background:#fff;border-radius:0 8px 8px 0;animation:rise .2s ease}
@keyframes rise{from{transform:translateY(6px);opacity:0}to{transform:none;opacity:1}}
.timeline li.ok{border-color:var(--ok)}.timeline li.warn{border-color:var(--warn);background:#fffbeb}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);font-size:14px}
tr:last-child td{border-bottom:0}
.tag{font-size:12px;color:var(--muted)}
/* --- conversational Run experience --- */
.chat{display:flex;flex-direction:column;height:70vh;background:#fff;border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;box-shadow:0 1px 3px rgba(15,23,42,.05)}
.chat-head{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0}
.chat-log{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:linear-gradient(#f8fafc,#fff)}
.msg{max-width:78%;padding:9px 13px;border-radius:14px;font-size:14px;line-height:1.45;animation:rise .18s ease;white-space:pre-wrap;word-break:break-word}
.msg.user{align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:4px}
.msg.assistant{align-self:flex-start;background:#f1f5f9;border-bottom-left-radius:4px}
.msg.action{align-self:flex-start;background:transparent;border:1px dashed var(--line);color:var(--muted);font-size:13px;max-width:88%}
.msg.approval{align-self:flex-start;background:#fffbeb;border:1px solid #fcd34d}
.msg.question{align-self:flex-start;background:#eff6ff;border:1px solid #bfdbfe}
.msg.resolved{opacity:.55}
.msg .tag{display:block;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}
.chat-input{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line);background:#fff}
.chat-input textarea{margin:0;resize:none;height:46px}
.chat-input .btn{flex:0 0 auto;padding:0 20px}
"""


def css2() -> str:
    """Layered polish that overrides the base stylesheet (prototype feel)."""
    return """
:root{--bg2:#111827;--ink2:#334155;--accent2:#3b82f6;--violet:#7c3aed;
--line:#e5e9f0;--line2:#eef2f7;--radius:14px;--shadow:0 1px 2px rgba(15,23,42,.06);
--shadow-lg:0 18px 40px rgba(15,23,42,.12);--ring:0 0 0 3px rgba(37,99,235,.22)}
html{scroll-behavior:smooth}
body{background:linear-gradient(180deg,#f8fafc,#eef2ff 62%,#f8fafc);min-height:100vh}
.side{width:230px;background:linear-gradient(180deg,#0f172a,#111827);padding:20px 14px;position:sticky;top:0;height:100vh}
.brand{margin:4px 6px 20px;font-weight:800;letter-spacing:.01em}
.brand .dot{animation:pulse 2.4s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 4px rgba(22,163,74,.22)}50%{box-shadow:0 0 0 8px rgba(22,163,74,.06)}}
.side a{display:flex;align-items:center;gap:10px;font-weight:500;border-radius:10px}
.side a.active{background:linear-gradient(135deg,var(--accent),var(--violet));box-shadow:0 10px 22px rgba(37,99,235,.4)}
.side-foot{padding:16px 8px 4px;border-top:1px solid #1e293b}
.top{background:rgba(255,255,255,.85);backdrop-filter:blur(9px);padding:13px 24px;box-shadow:0 4px 18px rgba(15,23,42,.03);z-index:20}
.toggle{border-radius:12px;padding:3px;gap:3px}
.toggle a{border-radius:9px}
.toggle a.on{background:#fff;color:var(--accent);box-shadow:0 4px 12px rgba(37,99,235,.18);font-weight:600}
.wrap{max-width:1220px}
.stack{display:flex;flex-direction:column;gap:16px}
.section{display:flex;align-items:center;gap:10px;margin:4px 0 12px;font-weight:700;font-size:15px}
.section::before{content:"";width:4px;height:18px;border-radius:3px;background:linear-gradient(var(--accent),var(--violet))}
.hint{color:var(--muted);font-size:12.5px;line-height:1.5;margin:6px 2px 0}
.card{border-radius:var(--radius);padding:18px;position:relative;overflow:hidden}
.card::before{content:"";position:absolute;left:0;top:0;height:3px;width:100%;background:linear-gradient(90deg,var(--accent),var(--violet));opacity:0;transition:opacity .22s ease}
.card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:#d7deea}
.card:hover::before{opacity:1}
.card h3{font-size:17px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 11px;font-weight:600}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.75}
.badge.status{background:#f1f5f9;color:var(--muted)}
.btn{overflow:hidden;border-radius:11px;padding:10px 17px;background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 6px 14px rgba(37,99,235,.22);transition:transform .14s ease,box-shadow .2s ease,filter .2s ease}
.btn:hover{transform:translateY(-2px);box-shadow:0 14px 26px rgba(37,99,235,.32);filter:saturate(1.08)}
.btn.ghost{box-shadow:0 2px 8px rgba(37,99,235,.08)}
.btn.ok{background:linear-gradient(135deg,var(--ok),#22c55e)}
.btn.btm{background:#f1f5f9;color:var(--ink2);border:1px solid var(--line);box-shadow:none}
.btn.btm:hover{background:#e8eef7;color:var(--ink)}
.btn.lg{padding:13px 22px;font-size:15px}
.ripple{position:absolute;border-radius:50%;transform:scale(0);background:rgba(255,255,255,.45);animation:ripple .55s ease-out;pointer-events:none}
.btn.ghost .ripple,.btn.btm .ripple,.btn.reject .ripple{background:rgba(37,99,235,.16)}
@keyframes ripple{to{transform:scale(2.6);opacity:0}}
input,select,textarea{border-radius:10px;padding:10px 12px;color:var(--ink)}
input:hover,select:hover,textarea:hover{border-color:#cbd5e1}
select{appearance:none;-webkit-appearance:none;cursor:pointer;padding-right:38px;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
background-repeat:no-repeat;background-position:right 12px center}
select.pop{transform:scale(1.015);border-color:var(--accent);box-shadow:var(--ring)}
label{color:var(--ink2);font-weight:600}
.canvas{background:radial-gradient(circle at 1px 1px,#e2e8f0 1px,transparent 0) 0 0/22px 22px,#fff}
.slide{width:380px;padding:22px;box-shadow:-10px 0 34px rgba(15,23,42,.12);z-index:40}
.timeline li{border-radius:0 10px 10px 0;box-shadow:var(--shadow)}
thead th{background:#f8fafc;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tbody tr{transition:background .14s ease}tbody tr:hover{background:#f8fafc}
code{background:#f1f5f9;padding:1px 6px;border-radius:6px;font-size:13px}
pre{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:12px;overflow:auto;font-size:13px;line-height:1.5}
.empty{text-align:center;padding:34px 18px;color:var(--muted)}
.empty .big{font-size:34px;margin-bottom:8px}
.chat{box-shadow:var(--shadow-lg)}
.chat-log{background:linear-gradient(#f8fafc,#fff);gap:11px}
.msg{border-radius:15px;padding:10px 14px;box-shadow:var(--shadow)}
.msg.user{background:linear-gradient(135deg,var(--accent),var(--accent2))}
.typing{align-self:flex-start;display:flex;gap:5px;padding:13px 16px;background:#f1f5f9;border-radius:15px;border-bottom-left-radius:5px}
.typing span{width:7px;height:7px;border-radius:50%;background:#94a3b8;animation:blink 1.3s infinite both}
.typing span:nth-child(2){animation-delay:.2s}.typing span:nth-child(3){animation-delay:.4s}
@keyframes blink{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
#toasts{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;gap:10px;z-index:60}
.toast{background:#0f172a;color:#fff;padding:12px 16px;border-radius:12px;box-shadow:var(--shadow-lg);font-size:14px;display:flex;align-items:center;gap:10px;animation:toastin .25s ease;min-width:200px;transition:opacity .25s ease,transform .25s ease}
.toast::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent2)}
.toast.ok::before{background:var(--ok)}.toast.bad::before{background:var(--bad)}.toast.warn::before{background:var(--warn)}
@keyframes toastin{from{transform:translateY(12px);opacity:0}to{transform:none;opacity:1}}
.card .actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.kv{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
@media(max-width:760px){.side{position:static;height:auto;width:100%}.app{flex-direction:column}.wrap{padding:16px}}
"""


def css3() -> str:
    """Interactive layer: flow editor, confirmation modal, button polish."""
    return """
/* --- buttons & cards --- */
.sm{padding:7px 12px;font-size:13px;border-radius:9px}
.card.leaving{opacity:0;transform:scale(.96);pointer-events:none;transition:opacity .25s ease,transform .25s ease}
.badge.mini{font-size:10px;padding:1px 7px;letter-spacing:.02em}
input[type=number]{-moz-appearance:textfield}
input[type=number]::-webkit-outer-spin-button,input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
/* --- confirmation modal --- */
.modal-back{position:fixed;inset:0;background:rgba(15,23,42,.45);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;z-index:80;padding:20px;animation:fadein .15s ease}
.modal-back[hidden]{display:none}
.modal{background:#fff;border-radius:16px;padding:22px 24px;width:min(430px,94vw);box-shadow:0 30px 60px rgba(15,23,42,.35);animation:rise .18s ease}
.modal h4{margin:0 0 6px;font-size:16px}
.modal p{margin:0;color:var(--muted);font-size:14px;line-height:1.5}
.modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}
@keyframes fadein{from{opacity:0}to{opacity:1}}
/* --- flow toolbar + node palette --- */
.flow-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:12px 0 10px}
.flow-bar .hint{margin:0;flex:1;min-width:240px}
.palette{display:flex;gap:20px;flex-wrap:wrap;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:16px;margin-bottom:12px;box-shadow:var(--shadow);animation:rise .16s ease}
.palette[hidden]{display:none}
.pal-group{min-width:150px}
.pal-cat{font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;margin-bottom:7px}
.pal-items{display:flex;flex-direction:column;gap:5px}
.pal-item{text-align:left;background:#fff;border:1px solid var(--line);border-radius:9px;padding:7px 11px;font:inherit;font-size:13px;cursor:pointer;color:var(--ink);transition:transform .12s ease,border-color .12s ease,background .12s ease,box-shadow .12s ease}
.pal-item:hover{border-color:var(--accent);background:#eff6ff;transform:translateX(3px);box-shadow:0 4px 10px rgba(37,99,235,.12)}
.pal-item:active{transform:translateX(3px) scale(.97)}
.pal-item:focus-visible{outline:none;box-shadow:var(--ring)}
.pal-item.is-loading{opacity:.55;pointer-events:none}
.pal-note{max-width:280px;min-width:210px}
/* --- flow canvas: draggable nodes + edges --- */
.canvas{height:70vh;min-height:380px}
.node{width:192px;padding:9px 11px;cursor:grab;z-index:2;touch-action:none}
.node:active{cursor:grabbing}
.node .node-head{display:flex;align-items:center;justify-content:space-between;gap:6px;min-height:18px}
.node strong{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:13.5px;line-height:1.3;margin-top:3px;word-break:break-word}
.node .node-x{border:0;background:transparent;color:var(--muted);font-size:17px;line-height:1;cursor:pointer;padding:0 5px;border-radius:6px;transition:color .12s ease,background .12s ease,transform .12s ease}
.node .node-x:hover{color:var(--bad);background:#fee2e2;transform:scale(1.15)}
.node .node-x:focus-visible{outline:none;box-shadow:var(--ring)}
.node .node-x.is-loading{opacity:.5;pointer-events:none}
.node .node-next{display:flex;align-items:center;gap:5px;margin-top:8px}
.node .flow-arrow{color:var(--muted);font-size:13px;flex:0 0 auto}
.node .next-sel{margin:0;padding:4px 24px 4px 7px;font-size:12px;border-radius:7px;background-color:#f8fafc;border-color:var(--line);text-overflow:ellipsis}
.node .next-sel:hover{border-color:var(--accent)}
.node.sel{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.18),0 12px 24px rgba(37,99,235,.16);z-index:6}
.node.dragging{opacity:.92;box-shadow:0 22px 40px rgba(15,23,42,.28);z-index:10;transition:none}
.node.t-trigger{border-color:var(--ok)}
.node.t-human{border-color:var(--warn)}
.node.t-ai{border-color:#a78bfa}
.node.t-finance{border-color:#60a5fa}
.node.t-logic{border-color:#94a3b8}
.node.t-output{border-color:#14b8a6}
.edges{position:absolute;left:0;top:0;pointer-events:none;overflow:visible;z-index:1}
.edge{fill:none;stroke:#94a3b8;stroke-width:2;stroke-linecap:round}
.edge.on{stroke:var(--accent);stroke-width:2.5}
.panel-actions .btn{flex:1}
"""


def _shell(active: str, body: str, mode: str = "configure") -> str:
    subtitles = {
        "Agents": "Create and configure your AI employees",
        "Flow": "Design what the employee does, step by step",
        "Run": "Give an employee a task and watch it work",
        "History": "Every past run, with its trace",
        "Logs": "Every logged trace: model calls, tools, approvals, verifier",
    }
    def link(name: str):
        href = "/" + name.lower()
        cls = "active" if name == active else ""
        return f'<a class="{cls}" href="{href}">{name}</a>'
    configure_on = "on" if mode == "configure" else ""
    use_on = "on" if mode == "use" else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{active} - AI Finance Employees</title><style>{css()}{css2()}{css3()}</style></head><body>
<div class="app">
  <aside class="side">
    <div class="brand"><span class="dot"></span>Finance Employees</div>
    <nav class="nav">{link("Agents")}{link("Flow")}{link("Run")}{link("History")}{link("Logs")}</nav>
    <div class="side-foot">AI Finance Employee Builder</div>
  </aside>
  <div class="main">
    <header class="top">
      <div class="top-title"><span class="subtitle">{subtitles.get(active, "")}</span></div>
      <div class="toggle">
        <a class="{configure_on}" href="/agents?mode=configure">Configure</a>
        <a class="{use_on}" href="/run?mode=use">Use</a>
      </div>
    </header>
    <div class="wrap">{body}</div>
  </div>
</div>
<div id="toasts"></div>
<div class="modal-back" id="modal" hidden>
  <div class="modal" role="dialog" aria-modal="true">
    <h4 class="modal-title"></h4><p class="modal-msg"></p>
    <div class="modal-actions">
      <button class="btn btm" id="m-no" type="button">Cancel</button>
      <button class="btn reject" id="m-yes" type="button">Delete</button>
    </div>
  </div>
</div>
<script>
function esc(s){{return String(s==null?'':s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));}}
function toast(msg,kind){{
  const wrap=document.getElementById('toasts');if(!wrap)return;
  const t=document.createElement('div');t.className='toast '+(kind||'');t.textContent=msg;
  wrap.appendChild(t);
  setTimeout(()=>{{t.style.opacity='0';t.style.transform='translateY(8px)';setTimeout(()=>t.remove(),260);}},2600);
}}
function askConfirm(title,msg,yesLabel){{
  return new Promise(res=>{{
    const m=document.getElementById('modal');if(!m){{res(false);return;}}
    m.querySelector('.modal-title').textContent=title||'Are you sure?';
    m.querySelector('.modal-msg').textContent=msg||'';
    const y=m.querySelector('#m-yes');y.textContent=yesLabel||'Confirm';
    const n=m.querySelector('#m-no');
    m.hidden=false;y.focus();
    const done=v=>{{m.hidden=true;y.removeEventListener('click',onYes);n.removeEventListener('click',onNo);m.removeEventListener('click',onBg);document.removeEventListener('keydown',onKey);res(v);}};
    const onYes=()=>done(true), onNo=()=>done(false);
    const onBg=e=>{{if(e.target===m)done(false);}};
    const onKey=e=>{{if(e.key==='Escape')done(false);else if(e.key==='Enter')done(true);}};
    y.addEventListener('click',onYes);n.addEventListener('click',onNo);m.addEventListener('click',onBg);document.addEventListener('keydown',onKey);
  }});
}}
async function loadPanel(url){{
  const p=document.getElementById('panel');if(!p)return;
  p.innerHTML='<div class="slide"><div class="tag">Loading…</div></div>';
  const r=await fetch(url);p.innerHTML=await r.text();
}}
async function post(url,data,btn){{
  if(btn)btn.classList.add('is-loading');
  try{{
    const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:new URLSearchParams(data||{{}})}});
    return await r.text();
  }}finally{{if(btn)btn.classList.remove('is-loading');}}
}}
let ES=null;
let RUN={{id:null,done:true,awaiting:null}};
function typing(on){{
  const log=document.getElementById('chat-log');if(!log)return;
  let t=document.getElementById('typing');
  if(on){{
    if(!t){{t=document.createElement('div');t.id='typing';t.className='typing';t.innerHTML='<span></span><span></span><span></span>';}}
    log.appendChild(t);log.scrollTop=log.scrollHeight;
  }}else if(t){{t.remove();}}
}}
function bubble(kind,html){{
  const log=document.getElementById('chat-log');if(!log)return;
  const d=document.createElement('div');d.className='msg '+kind;d.innerHTML=html;
  log.appendChild(d);
  const t=document.getElementById('typing');if(t)log.appendChild(t);
  log.scrollTop=log.scrollHeight;
}}
function setStatus(text,cls){{
  const el=document.getElementById('chat-status');
  if(el){{el.className='badge status '+(cls||'');el.textContent=text;}}
  if(text==='working'){{RUN.awaiting=null;typing(true);}}else typing(false);
}}
function chatSend(ev,empId){{
  ev.preventDefault();
  const box=document.getElementById('chat-text');
  const t=(box.value||'').trim();if(!t)return false;
  if(!RUN.done){{
    // A live run owns the main box: answer its pending interruption instead
    // of silently starting a competing second run.
    if(RUN.awaiting==='approval'){{
      const v=t.toLowerCase().replace(/[.,!]+$/g,'').trim();
      if(/^(yes|y|approve|approved|ok|okay|go ahead|sure)$/.test(v)){{
        box.value='';bubble('user',esc(t));answerApproval(RUN.id,'yes');return false;}}
      if(/^(no|n|reject|rejected|stop|cancel)$/.test(v)){{
        box.value='';bubble('user',esc(t));answerApproval(RUN.id,'no');return false;}}
      toast('An approval needs yes or no — or use the Approve/Reject buttons','warn');
      return false;
    }}
    if(RUN.awaiting==='question'){{
      box.value='';answerReply(RUN.id,t);return false;
    }}
    toast("I'm still working on the previous task — one task at a time",'warn');
    return false;
  }}
  bubble('user',esc(t));box.value='';
  setStatus('working','warn');
  toast('Task sent to '+(empId||'employee'));
  RUN={{id:null,done:false,awaiting:null}};
  const fd=new URLSearchParams({{emp_id:empId,task:t}});
  fetch('/api/run/start',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:fd}})
    .then(r=>r.json())
    .then(j=>{{if(j.error){{RUN.done=true;bubble('assistant',esc(j.error));setStatus('failed','bad');toast(j.error,'bad');return;}}openStream(j.run_id);}})
    .catch(e=>{{RUN.done=true;bubble('assistant',esc('Error: '+e));setStatus('failed','bad');toast('Could not start the run','bad');}});
  return false;
}}
function openStream(runId){{
  if(ES)ES.close();
  RUN={{id:runId,done:false,awaiting:null}};
  ES=new EventSource('/run/stream/'+runId);
  ES.addEventListener('assistant',e=>bubble('assistant',esc(JSON.parse(e.data).text)));
  ES.addEventListener('action',e=>{{const d=JSON.parse(e.data);bubble('action','<span class="tag">step '+d.step+'</span>'+esc(d.text));}});
  ES.addEventListener('approval',e=>{{RUN.awaiting='approval';bubble('approval',JSON.parse(e.data).html);setStatus('needs approval','warn');toast('Your approval is needed','warn');}});
  ES.addEventListener('question',e=>{{RUN.awaiting='question';bubble('question',JSON.parse(e.data).html);setStatus('needs input','warn');}});
  ES.addEventListener('status',e=>{{const d=JSON.parse(e.data);setStatus(d.status,d.cls||'');}});
  ES.addEventListener('done',e=>{{RUN.done=true;RUN.awaiting=null;setStatus('done','ok');toast('Run finished','ok');ES.close();ES=null;}});
}}
function resolveCard(sel){{
  const m=document.querySelector('#chat-log '+sel+':not(.resolved)');
  if(m)m.classList.add('resolved');
}}
function answerApproval(runId,decision){{
  fetch('/run/approve/'+runId+'?decision='+decision,{{method:'POST'}})
    .then(()=>{{resolveCard('.msg.approval');setStatus('working','warn');
      toast(decision==='yes'?'Approved':'Rejected',decision==='yes'?'ok':'warn');}})
    .catch(()=>toast('Could not send the decision','bad'));
}}
function answerReply(runId,text){{
  fetch('/run/reply/'+runId+'?answer='+encodeURIComponent(text),{{method:'POST'}})
    .then(()=>{{resolveCard('.msg.question');bubble('user',esc(text));setStatus('working','warn');toast('Answer sent','ok');}})
    .catch(()=>toast('Could not send the answer','bad'));
}}
function approve(runId,decision,btn){{
  if(btn)btn.classList.add('is-loading');
  answerApproval(runId,decision);
}}
function reply(runId,btn){{
  const wrap=btn.closest('.msg');const inp=wrap.querySelector('input');
  const v=(inp.value||'').trim();if(!v)return;
  if(btn)btn.classList.add('is-loading');
  answerReply(runId,v);
}}
const MODEL_INFO={{groq:'Fast hosted model (Groq gpt-oss-120b) — best for a quick demo.',
local:'Runs on your own machine via Ollama — private, but slower.',
stub:'Offline demo model — deterministic and free, ideal for testing.',
openai:'Optional hosted model; needs an OpenAI key.'}};
function syncModelHint(){{
  const sel=document.getElementById('model-select');const h=document.getElementById('model-hint');
  if(sel&&h)h.textContent=MODEL_INFO[sel.value]||'';
}}
function enhanceSelects(){{
  document.querySelectorAll('select').forEach(s=>s.addEventListener('change',()=>{{
    s.classList.add('pop');setTimeout(()=>s.classList.remove('pop'),180);
  }}));
}}
document.addEventListener('click',(e)=>{{
  const b=e.target.closest('.btn');if(!b)return;
  const r=b.getBoundingClientRect();const sp=document.createElement('span');sp.className='ripple';
  const d=Math.max(r.width,r.height);sp.style.width=sp.style.height=d+'px';
  sp.style.left=(e.clientX-r.left-d/2)+'px';sp.style.top=(e.clientY-r.top-d/2)+'px';
  b.appendChild(sp);setTimeout(()=>sp.remove(),600);
  if(b.disabled||b.dataset.noload!==undefined||b.getAttribute('onclick'))return;
  if(b.tagName==='A'||b.type==='submit'){{b.classList.add('is-loading');setTimeout(()=>b.classList.remove('is-loading'),4000);}}
}});
document.addEventListener('DOMContentLoaded',()=>{{
  enhanceSelects();syncModelHint();
  const q=new URLSearchParams(location.search);
  if(q.get('saved'))toast('Changes saved','ok');
  if(q.get('created'))toast('Employee created','ok');
}});
</script></body></html>"""


def _employees() -> list[Employee]:
    return store.list_employees()


def _report_dict(rep) -> dict:
    if rep is None:
        return {}
    if hasattr(rep, "model_dump"):
        return rep.model_dump()
    return rep if isinstance(rep, dict) else {}


MODEL_LABELS = {
    "groq": "Groq (fast, hosted)",
    "local": "Local machine (Ollama)",
    "stub": "Offline demo (no key)",
    "openai": "OpenAI (optional)",
}
STATUS_LABELS = {"running": "Working", "completed": "Done", "failed": "Needs attention",
                 "waiting_approval": "Waiting for you", "interrupted": "Stopped"}


_SEED_FLAG = os.path.join(STORAGE_DIR, ".demo_seed_off")


def _seed() -> None:
    """Seed the demo employee on first run — but never resurrect it after the
    user has explicitly deleted every employee."""
    if _employees() or os.path.exists(_SEED_FLAG):
        return
    store.save_employee(new_employee("Accounts Payable Employee", "Accounts Payable",
                                     "Process incoming invoices and prepare them for payment."))


def _suppress_seed() -> None:
    if _employees():
        return
    try:
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(_SEED_FLAG, "w") as fh:
            fh.write("1")
    except OSError:
        pass


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging(STORAGE_DIR)
    log_event("dashboard_started", store=STORE_PATH, app=APP_BASE_URL)
    _seed()
    yield


# --------------------------------------------------------------------------- #
# screens
# --------------------------------------------------------------------------- #
app = FastAPI(title="AI Finance Employee Builder", lifespan=_lifespan)


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse("/agents")


@app.get("/flow", response_class=HTMLResponse)
def flow_home(mode: str = "configure"):
    employees = _employees()
    if not employees:
        return RedirectResponse("/agents")
    return RedirectResponse(f"/flow/{employees[0].id}?mode={mode}")


@app.get("/agents", response_class=HTMLResponse)
def agents(mode: str = "configure"):
    _seed()
    cards = ""
    for e in _employees():
        cards += f"""<div class="card" data-emp="{_esc(e.id)}">
          <div class="kv"><h3 style="margin:0">{e.name}</h3><span class="badge">{_esc(e.role)}</span></div>
          <p class="muted" style="margin:10px 0">{_esc(e.description) or 'No description yet'}</p>
          <p class="kv"><span class="badge ok">Ready</span>
            <span class="tag">Thinking with {_esc(MODEL_LABELS.get(e.model, e.model))}</span></p>
          <div class="actions">
            <a class="btn ghost" href="/agents/{e.id}?mode=configure">Edit setup</a>
            <a class="btn" href="/run/{e.id}?mode=use">Run this employee</a>
            <button class="btn btm sm" type="button" data-noload
              onclick="dupEmployee(this)">Duplicate</button>
            <button class="btn reject sm" type="button" data-noload
              onclick="deleteEmployee(this)">Delete</button></div></div>"""
    if not cards:
        cards = ('<div class="empty" style="grid-column:1/-1"><div class="big">👋</div>'
                 'No employees yet — create your first one below.</div>')
    role_opts = ''.join(f'<option>{r}</option>' for r in ROLE_TEMPLATES)
    body = f"""<div class="section">Your AI employees</div>
      <p class="hint">An <b>AI employee</b> is an AI teammate with a role, a set of allowed tools,
      and spending rules. Use <b>Edit setup</b> to change how it works, or <b>Run this employee</b>
      to hand it a task in plain English.</p>
      <div class="grid" style="margin:14px 0 6px">{cards}</div>
      <div class="section" style="margin-top:24px">Create a new employee</div>
      <div class="card">
        <form method="post" action="/agents" onsubmit="return createEmployee(event)">
          <div class="row">
            <div><label>Name</label><input name="name" placeholder="Accounts Payable Employee" required>
              <div class="hint">A human-friendly name for this teammate.</div></div>
            <div><label>Role</label><select name="role">{role_opts}</select>
              <div class="hint">The role sets the default steps and tools.</div></div>
          </div>
          <label>What should it do?</label>
          <input name="description" placeholder="Process incoming invoices and prepare them for payment">
          <div class="hint">A one-line description in plain language.</div>
          <div class="actions"><button class="btn lg" type="submit" data-noload>Create employee</button></div>
        </form>
      </div>
      <script>
      async function createEmployee(ev){{
        ev.preventDefault();
        const form=ev.target, btn=form.querySelector('button[type=submit]');
        btn.classList.add('is-loading');
        try{{
          const fd=new URLSearchParams(new FormData(form));
          const r=await fetch('/agents',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'}},body:fd}});
          const j=await r.json().catch(()=>null);
          if(!r.ok||!j||j.error)throw new Error((j&&j.error)||'Could not create the employee');
          toast('Employee created','ok');
          location.href='/agents/'+j.id+'?created=1';
        }}catch(e){{
          toast(e.message||'Could not create the employee','bad');
          btn.classList.remove('is-loading');
        }}
        return false;
      }}
      function empIdOf(btn){{
        const card=btn.closest('.card');
        return card?card.dataset.emp:'';
      }}
      async function dupEmployee(btn){{
        const id=empIdOf(btn);if(!id)return;
        btn.classList.add('is-loading');
        try{{
          const r=await fetch('/agents/'+encodeURIComponent(id)+'/duplicate',{{method:'POST'}});
          const j=await r.json().catch(()=>null);
          if(!r.ok||!j||j.error)throw new Error((j&&j.error)||'Could not duplicate the employee');
          location.href='/agents/'+j.id+'?created=1';
        }}catch(e){{toast(e.message||'Could not duplicate the employee','bad');btn.classList.remove('is-loading');}}
      }}
      async function deleteEmployee(btn){{
        const id=empIdOf(btn);if(!id)return;
        const ok=await askConfirm('Delete this employee?','Its setup and workflow are removed. Past runs stay in History.','Delete employee');
        if(!ok)return;
        btn.classList.add('is-loading');
        try{{
          const r=await fetch('/agents/'+encodeURIComponent(id)+'/delete',{{method:'POST'}});
          const j=await r.json().catch(()=>null);
          if(!r.ok||!j||j.error)throw new Error((j&&j.error)||'Could not delete the employee');
          const card=btn.closest('.card');
          card.classList.add('leaving');
          setTimeout(()=>{{
            card.remove();
            const grid=document.querySelector('.grid');
            if(grid&&!grid.querySelector('.card')){{
              const d=document.createElement('div');
              d.className='empty';d.style.gridColumn='1/-1';
              d.innerHTML='<div class="big">👋</div>No employees yet — create your first one below.';
              grid.appendChild(d);
            }}
          }},260);
          toast('Employee deleted','ok');
        }}catch(e){{toast(e.message||'Could not delete the employee','bad');btn.classList.remove('is-loading');}}
      }}
      </script>"""
    return _shell("Agents", body, mode)


@app.post("/agents")
async def create_employee(request: Request, name: str = Form(...), role: str = Form(...),
                          description: str = Form("")):
    emp = store.save_employee(new_employee(name, role, description))
    log_event("employee_created", employee=emp.id, role=role)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"id": emp.id, "name": emp.name})
    return RedirectResponse(f"/agents/{emp.id}?created=1", status_code=303)


@app.post("/agents/{emp_id}/duplicate")
def duplicate_employee(emp_id: str):
    e = store.get_employee(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    base = f"{e.name} (copy)"
    new_id = _slug(base)
    i = 2
    while new_id == emp_id or store.get_employee(new_id):
        new_id = _slug(f"{base} {i}")
        i += 1
    data = e.model_dump()
    data.update(id=new_id, name=base, created_at=time.time(), updated_at=time.time())
    emp = store.save_employee(Employee(**data))
    log_event("employee_duplicated", employee=emp.id, source=emp_id)
    return JSONResponse({"id": emp.id, "name": emp.name})


@app.post("/agents/{emp_id}/delete")
def delete_employee(emp_id: str):
    e = store.get_employee(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    store.delete_employee(emp_id)
    _suppress_seed()
    log_event("employee_deleted", employee=emp_id)
    return JSONResponse({"ok": True, "id": emp_id})


@app.get("/agents/{emp_id}", response_class=HTMLResponse)
def configure(emp_id: str, mode: str = "configure"):
    e = store.get_employee(emp_id)
    if not e:
        return RedirectResponse("/agents")
    from ai_operator.tools import TOOLS
    tool_boxes = "".join(
        f'<label style="color:var(--ink);font-weight:500"><input type="checkbox" name="tool" value="{t}" '
        f'{"checked" if t in e.tools else ""} style="width:auto;margin-right:6px"> '
        f'{_esc(TOOL_LABELS.get(t, t))}</label>'
        for t in sorted(TOOLS))
    steps = ''.join(f'<li>{_esc(n.name)}</li>' for n in (e.flow.nodes if e.flow else []))
    body = f"""<div class="row"><div style="flex:2"><div class="card">
      <div class="kv"><h3 style="margin:0">{e.name}</h3><span class="badge">{_esc(e.role)}</span></div>
      <p class="hint">Set how this employee behaves, which model thinks for it, and how much
      it may spend before it must ask you for approval. Instructions, tools, thresholds and
      the visual workflow are <b>compiled into the runtime</b> — they are not decoration.</p>
      <form method="post" action="/agents/{e.id}">
        <label>Instructions</label>
        <textarea name="instructions" rows="3">{e.instructions}</textarea>
        <div class="hint">Standing orders injected into every decision prompt.</div>
        <label>AI model</label>
        <select name="model" id="model-select">
          <option value="groq" {'selected' if e.model=='groq' else ''}>Groq — fast, hosted (gpt-oss-120b)</option>
          <option value="local" {'selected' if e.model=='local' else ''}>Local machine — Ollama / vLLM</option>
          <option value="stub" {'selected' if e.model=='stub' else ''}>Offline demo — no key needed</option>
          <option value="openai" {'selected' if e.model=='openai' else ''}>OpenAI — optional</option>
        </select>
        <div class="hint" id="model-hint"></div>
        <div class="row">
          <div><label>Auto-approve below ({e.currency})</label>
            <input name="auto_threshold" type="number" min="0" step="1000" value="{e.auto_threshold}">
            <div class="hint">Actions under this amount run without asking you.</div></div>
          <div><label>Ask me above ({e.currency})</label>
            <input name="approval_threshold" type="number" min="0" step="1000" value="{e.approval_threshold}">
            <div class="hint">Larger amounts pause and wait for your approval.</div></div>
        </div>
        <label>Tools it may use</label>
        <div class="hint">Tick the capabilities this employee is allowed to use.</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:4px;margin-top:6px">{tool_boxes}</div>
        <div class="actions">
          <button class="btn" type="submit">Save changes</button>
          <a class="btn btm" href="/flow/{e.id}?mode=configure">Edit workflow</a>
          <a class="btn ok" href="/run/{e.id}?mode=use">Run this employee</a></div>
      </form></div></div>
      <div><div class="card"><h3>Workflow steps</h3>
        <p class="hint">The sequence this employee follows to get work done.</p>
        <ul>{steps}</ul></div></div></div>"""
    return _shell("Agents", body, mode)


@app.post("/agents/{emp_id}")
async def save_employee(emp_id: str, request: Request):
    e = store.get_employee(emp_id)
    if not e:
        return RedirectResponse("/agents")
    form = await request.form()
    e.instructions = form.get("instructions", e.instructions)
    e.model = form.get("model", e.model)
    e.auto_threshold = float(form.get("auto_threshold", e.auto_threshold))
    e.approval_threshold = float(form.get("approval_threshold", e.approval_threshold))
    e.tools = list(form.getlist("tool"))
    e.updated_at = time.time()
    store.save_employee(e)
    return RedirectResponse(f"/agents/{emp_id}?saved=1", status_code=303)


NODE_TYPES = ["trigger", "ai", "finance", "logic", "human", "output"]


def _default_pos(i: int) -> tuple[int, int]:
    """Server-side fallback layout: columns of 6, 96px rows, 210px columns."""
    return 20 + (i // 6) * 210, 40 + (i % 6) * 96


def _node_pos(n, i: int) -> tuple[float, float]:
    x, y = n.config.get("x"), n.config.get("y")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)):
        return float(x), float(y)
    x, y = _default_pos(i)
    return float(x), float(y)


def _flow_dict(e) -> dict:
    """The flow payload the canvas renders from (positions included)."""
    fc = e.flow
    nodes = []
    for i, n in enumerate(fc.nodes):
        x, y = _node_pos(n, i)
        nodes.append({"id": n.id, "type": n.type, "name": n.name,
                      "x": round(x), "y": round(y),
                      "note": n.config.get("instructions", "")})
    return {"id": e.id, "start": fc.start, "nodes": nodes,
            "edges": [{"source": ed.source, "target": ed.target, "label": ed.label}
                      for ed in fc.edges]}


def _load_flow(emp_id: str):
    e = store.get_employee(emp_id)
    if not e or not e.flow:
        return None, None
    return e, e.flow


def _new_node_id(fc) -> str:
    import re
    used = {n.id for n in fc.nodes}
    nums = [int(m.group(1)) for i in used if (m := re.fullmatch(r"n(\d+)", i))]
    i = max(nums, default=0) + 1
    while f"n{i}" in used:
        i += 1
    return f"n{i}"


def _save_flow(e) -> None:
    e.updated_at = time.time()
    store.save_employee(e)


FLOW_JS = r"""
(function(){
  const canvas=document.getElementById('canvas');
  if(!canvas||!window.FLOW)return;
  const NS='http://www.w3.org/2000/svg', EMP=window.EMP_ID, LIB=window.LIB||{};
  const svg=document.createElementNS(NS,'svg');
  svg.setAttribute('class','edges');
  canvas.appendChild(svg);
  let selected=null, openId=null;
  const E=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const byId=id=>FLOW.nodes.find(n=>n.id===id);
  const nextOf=id=>{const e=FLOW.edges.find(x=>x.source===id);return e?e.target:'';};

  /* ---------- edges ---------- */
  function drawEdges(){
    const els={};
    canvas.querySelectorAll('.node').forEach(el=>els[el.dataset.id]=el);
    while(svg.firstChild)svg.removeChild(svg.firstChild);
    const defs=document.createElementNS(NS,'defs');
    [['arw','#94a3b8'],['arw-on','#2563eb']].forEach(([id,color])=>{
      const mk=document.createElementNS(NS,'marker');
      mk.setAttribute('id',id);mk.setAttribute('viewBox','0 0 10 10');
      mk.setAttribute('refX','9');mk.setAttribute('refY','5');
      mk.setAttribute('markerWidth','7');mk.setAttribute('markerHeight','7');
      mk.setAttribute('orient','auto-start-reverse');
      const p=document.createElementNS(NS,'path');
      p.setAttribute('d','M0 0L10 5L0 10z');p.setAttribute('fill',color);
      mk.appendChild(p);defs.appendChild(mk);
    });
    svg.appendChild(defs);
    let w=Math.max(canvas.clientWidth||600,240), h=Math.max(canvas.clientHeight||400,240);
    FLOW.nodes.forEach(n=>{const el=els[n.id];if(el){w=Math.max(w,n.x+el.offsetWidth+110);h=Math.max(h,n.y+el.offsetHeight+110);}});
    svg.setAttribute('width',w);svg.setAttribute('height',h);
    FLOW.edges.forEach(e=>{
      const a=byId(e.source), b=byId(e.target), ea=els[e.source], eb=els[e.target];
      if(!a||!b||!ea||!eb)return;
      const ax=a.x, ay=a.y, aw=ea.offsetWidth, ah=ea.offsetHeight;
      const bx=b.x, by=b.y, bw=eb.offsetWidth, bh=eb.offsetHeight;
      let x1,y1,x2,y2,c1x,c1y,c2x,c2y;
      if(by>=ay+ah-12){const d=Math.max(36,by-(ay+ah));x1=ax+aw/2;y1=ay+ah;x2=bx+bw/2;y2=by-9;c1x=x1;c1y=y1+d*.45;c2x=x2;c2y=y2-d*.45;}
      else if(bx>=ax+aw-12){const d=Math.max(36,bx-(ax+aw));x1=ax+aw+3;y1=ay+ah/2;x2=bx-9;y2=by+bh/2;c1x=x1+d*.45;c1y=y1;c2x=x2-d*.45;c2y=y2;}
      else if(bx+bw<=ax+12){const d=Math.max(36,ax-(bx+bw));x1=ax-3;y1=ay+ah/2;x2=bx+bw+9;y2=by+bh/2;c1x=x1-d*.45;c1y=y1;c2x=x2+d*.45;c2y=y2;}
      else{const d=Math.max(36,ay-(by+bh));x1=ax+aw/2;y1=ay-3;x2=bx+bw/2;y2=by+bh+9;c1x=x1;c1y=y1-d*.45;c2x=x2;c2y=y2+d*.45;}
      const path=document.createElementNS(NS,'path');
      path.setAttribute('d','M '+x1+' '+y1+' C '+c1x+' '+c1y+', '+c2x+' '+c2y+', '+x2+' '+y2);
      path.setAttribute('class','edge'+(selected===e.source?' on':''));
      path.setAttribute('marker-end','url(#'+(selected===e.source?'arw-on':'arw')+')');
      svg.appendChild(path);
    });
  }

  /* ---------- nodes ---------- */
  function nodeEl(n){
    const el=document.createElement('div');
    el.className='node t-'+E(n.type)+(selected===n.id?' sel':'');
    el.dataset.id=n.id;
    el.style.left=n.x+'px';el.style.top=n.y+'px';
    let opts='<option value="">— end —</option>';
    FLOW.nodes.forEach(t=>{
      if(t.id===n.id)return;
      opts+='<option value="'+E(t.id)+'"'+(t.id===nextOf(n.id)?' selected':'')+'>'
           +E(t.name)+(t.id===FLOW.start?' ◆start':'')+'</option>';
    });
    const head=n.id===FLOW.start
      ? '<span class="tag">'+E(n.type)+'</span><span class="badge ok mini">start</span>'
      : '<span class="tag">'+E(n.type)+'</span><button class="node-x" type="button" title="Delete step" aria-label="Delete step">&times;</button>';
    el.innerHTML='<div class="node-head">'+head+'</div><strong>'+E(n.name)+'</strong>'
      +'<div class="node-next"><span class="flow-arrow" title="Hands over to">▸</span>'
      +'<select class="next-sel" data-id="'+E(n.id)+'" title="Choose the next step">'+opts+'</select></div>';
    bind(el);
    return el;
  }

  function render(){
    canvas.querySelectorAll('.node').forEach(el=>el.remove());
    FLOW.nodes.forEach(n=>canvas.appendChild(nodeEl(n)));
    drawEdges();
  }
  window.applyFlow=function(f){window.FLOW=f;if(selected&&!byId(selected))selected=null;render();};

  async function refetch(){
    try{const r=await fetch('/api/flow/'+EMP);const j=await r.json();if(j.flow)window.FLOW=j.flow;render();}catch(e){}
  }

  function bind(el){
    el.addEventListener('pointerdown',ev=>{
      if(ev.target.closest('.node-x,.next-sel'))return;
      if(ev.pointerType==='mouse'&&ev.button!==0)return;
      const n=byId(el.dataset.id);if(!n)return;
      selected=n.id;
      canvas.querySelectorAll('.node.sel').forEach(x=>x.classList.remove('sel'));
      el.classList.add('sel');
      const sx=ev.clientX, sy=ev.clientY, ox=n.x, oy=n.y;
      let moved=false;
      try{el.setPointerCapture(ev.pointerId);}catch(_){}
      el.classList.add('dragging');
      const move=m=>{
        const dx=m.clientX-sx, dy=m.clientY-sy;
        if(!moved&&Math.abs(dx)+Math.abs(dy)<5)return;
        moved=true;
        n.x=Math.max(0,Math.round(ox+dx));n.y=Math.max(0,Math.round(oy+dy));
        el.style.left=n.x+'px';el.style.top=n.y+'px';
        drawEdges();
      };
      const up=()=>{
        el.classList.remove('dragging');
        el.removeEventListener('pointermove',move);
        el.removeEventListener('pointerup',up);
        el.removeEventListener('pointercancel',up);
        if(moved){el._moved=1;savePositions({[n.id]:{x:n.x,y:n.y}});}
        else{drawEdges();}
      };
      el.addEventListener('pointermove',move);
      el.addEventListener('pointerup',up);
      el.addEventListener('pointercancel',up);
    });
    el.addEventListener('click',ev=>{
      if(ev.target.closest('.node-x,.next-sel'))return;
      if(el._moved){el._moved=0;return;}
      openPanel(el.dataset.id);
    });
  }

  /* ---------- panel ---------- */
  function openPanel(id){
    openId=id;selected=id;render();
    loadPanel('/flow/'+EMP+'/node/'+encodeURIComponent(id));
  }
  window.closePanel=function(){openId=null;const p=document.getElementById('panel');if(p)p.innerHTML='';};

  window.saveNode=async function(id,btn){
    const name=document.getElementById('pf-name'), type=document.getElementById('pf-type'),
          note=document.getElementById('pf-note'), nxt=document.getElementById('pf-next');
    if(!name||!type)return;
    btn.classList.add('is-loading');
    try{
      const fd=new URLSearchParams({name:(name.value||'').trim()||'Step',type:type.value,
        instructions:note?note.value:'',next:nxt?nxt.value:''});
      const r=await fetch('/api/flow/'+EMP+'/nodes/'+encodeURIComponent(id),
        {method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:fd});
      const j=await r.json().catch(()=>({}));
      if(!r.ok||j.error)throw new Error(j.error||'Could not save the step');
      applyFlow(j.flow);
      toast('Step updated','ok');
      openPanel(id);   /* refresh the panel from server truth */
    }catch(e){toast(e.message||'Could not save the step','bad');btn.classList.remove('is-loading');}
  };

  window.deleteNode=async function(id,btn){
    if(id===FLOW.start){toast('The start step cannot be deleted','warn');return;}
    const n=byId(id);if(!n)return;
    const ok=await askConfirm('Delete “'+n.name+'”?','The step and its connections are removed from this workflow.','Delete step');
    if(!ok)return;
    if(btn)btn.classList.add('is-loading');
    try{
      const r=await fetch('/api/flow/'+EMP+'/nodes/'+encodeURIComponent(id)+'/delete',{method:'POST'});
      const j=await r.json().catch(()=>({}));
      if(!r.ok||j.error)throw new Error(j.error||'Could not delete the step');
      if(openId===id)window.closePanel();
      applyFlow(j.flow);
      toast('“'+n.name+'” deleted','ok');
    }catch(e){toast(e.message||'Could not delete the step','bad');if(btn)btn.classList.remove('is-loading');}
  };

  /* ---------- palette: create nodes ---------- */
  window.togglePalette=function(){
    const p=document.getElementById('palette');if(!p)return;
    if(!p.dataset.built){buildPalette(p);p.dataset.built='1';}
    p.hidden=!p.hidden;
  };
  function buildPalette(p){
    let h='';
    Object.keys(LIB).forEach(cat=>{
      h+='<div class="pal-group"><div class="pal-cat">'+E(cat)+'</div><div class="pal-items">';
      LIB[cat].forEach(name=>{
        h+='<button type="button" class="pal-item" data-type="'+E(cat)+'" data-name="'+E(name)+'">'+E(name)+'</button>';
      });
      h+='</div></div>';
    });
    h+='<div class="pal-group pal-note"><div class="pal-cat">Tip</div>'
      +'<p class="hint" style="margin:0">A new step is inserted right after the step you last selected '
      +'— if that step had no next yet. Otherwise it lands on the canvas unconnected; wire it up with '
      +'its <b>▸</b> selector.</p></div>';
    p.innerHTML=h;
    p.addEventListener('click',ev=>{
      const b=ev.target.closest('.pal-item');if(!b)return;
      addNode(b.dataset.type,b.dataset.name,b);
    });
  }
  async function addNode(type,name,btn){
    btn.classList.add('is-loading');
    try{
      const fd=new URLSearchParams({type:type,name:name,after:selected||''});
      const r=await fetch('/api/flow/'+EMP+'/nodes',
        {method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:fd});
      const j=await r.json().catch(()=>({}));
      if(!r.ok||j.error)throw new Error(j.error||'Could not add the step');
      applyFlow(j.flow);
      const p=document.getElementById('palette');if(p)p.hidden=true;
      toast('“'+name+'” added','ok');
      openPanel(j.node.id);
    }catch(e){toast(e.message||'Could not add the step','bad');}
    finally{btn.classList.remove('is-loading');}
  }

  /* ---------- positions & connections ---------- */
  async function savePositions(pos){
    try{
      const r=await fetch('/api/flow/'+EMP+'/positions',
        {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({positions:pos})});
      const j=await r.json().catch(()=>({}));
      if(!r.ok||j.error)throw new Error(j.error||'Could not save the layout');
      return true;
    }catch(e){toast(e.message||'Could not save the layout','bad');return false;}
  }
  async function connect(id,tgt){
    const snapshot=JSON.stringify(FLOW.edges);
    FLOW.edges=FLOW.edges.filter(e=>e.source!==id);
    if(tgt&&tgt!==id)FLOW.edges.push({source:id,target:tgt,label:''});
    drawEdges();
    try{
      const r=await fetch('/api/flow/'+EMP+'/nodes/'+encodeURIComponent(id),
        {method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
         body:new URLSearchParams({next:tgt||''})});
      const j=await r.json().catch(()=>({}));
      if(!r.ok||j.error)throw new Error(j.error||'Could not save the connection');
      applyFlow(j.flow);
      const a=byId(id), b=tgt?byId(tgt):null;
      toast(b?(a.name+' → '+b.name):(a.name+' now ends the flow'),'ok');
    }catch(e){
      FLOW.edges=JSON.parse(snapshot);
      render();
      toast(e.message||'Could not save the connection','bad');
    }
  }

  window.autoArrange=function(btn){
    if(btn)btn.classList.add('is-loading');
    const order=[], seen=new Set();
    let cur=FLOW.start;
    while(cur&&!seen.has(cur)){seen.add(cur);order.push(cur);const e=FLOW.edges.find(x=>x.source===cur);cur=e?e.target:'';}
    FLOW.nodes.forEach(n=>{if(!seen.has(n.id)){seen.add(n.id);order.push(n.id);}});
    const pos={};
    order.forEach((id,i)=>{const n=byId(id);if(!n)return;n.x=20+Math.floor(i/6)*210;n.y=40+(i%6)*96;pos[id]={x:n.x,y:n.y};});
    render();
    savePositions(pos).then(ok=>{if(ok)toast('Workflow arranged','ok');if(btn)btn.classList.remove('is-loading');});
  };

  /* ---------- wiring ---------- */
  canvas.addEventListener('click',ev=>{
    const x=ev.target.closest('.node-x');
    if(!x)return;
    ev.stopPropagation();
    const el=x.closest('.node');
    if(el)deleteNode(el.dataset.id,x);
  });
  canvas.addEventListener('change',ev=>{
    const sel=ev.target.closest?ev.target.closest('.next-sel'):null;
    if(!sel)return;
    connect(sel.dataset.id,sel.value);
  });
  document.addEventListener('DOMContentLoaded',render);
  if(document.readyState!=='loading')render();
})();
"""


@app.get("/flow/{emp_id}", response_class=HTMLResponse)
def flow(emp_id: str, mode: str = "configure"):
    e = store.get_employee(emp_id)
    if not e or not e.flow:
        return RedirectResponse("/agents")
    payload = json.dumps(_flow_dict(e)).replace("<", "\\u003c").replace(">", "\\u003e")
    lib = json.dumps(NODE_LIBRARY).replace("<", "\\u003c").replace(">", "\\u003e")
    body = (f"""<div class="section">Workflow — {_esc(e.name)}</div>
      <p class="hint">This diagram <b>is the employee's SOP</b>: the first connection from each
      step is the path that is compiled. Matching tools move the pointer forward; a
      <b>human</b> step pauses the run until you answer. Logic / trigger steps are skipped.
      Extra branches are stored as notes, not executed. <b>Drag</b> to move, <b>▸</b> to
      connect, <b>Add step</b> to create, <b>×</b> to delete. Click a step to edit it.</p>
      <div class="flow-bar">
        <button class="btn" type="button" data-noload onclick="togglePalette()">＋ Add step</button>
        <button class="btn btm" type="button" data-noload onclick="autoArrange(this)">Auto-arrange</button>
        <span class="kv"><span class="badge ok">start</span><span class="badge warn">human approval</span></span>
        <span class="hint">Changes save as you make them.</span>
      </div>
      <div class="palette" id="palette" hidden></div>
      <div class="canvas" id="canvas"></div>
      <div id="panel"></div>"""
        + f"<script>window.FLOW={payload};window.LIB={lib};window.EMP_ID={json.dumps(emp_id)};</script>"
        + "<script>" + FLOW_JS + "</script>")
    return _shell("Flow", body, mode)


@app.get("/api/flow/{emp_id}", response_class=JSONResponse)
def api_flow(emp_id: str):
    e, _ = _load_flow(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee or flow"}, status_code=404)
    return JSONResponse({"flow": _flow_dict(e)})


@app.get("/flow/{emp_id}/node/{node_id}", response_class=HTMLResponse)
def node_panel(emp_id: str, node_id: str):
    e, fc = _load_flow(emp_id)
    node = next((n for n in (fc.nodes if fc else []) if n.id == node_id), None)
    if not node:
        return HTMLResponse("<div class='slide'>Node not found</div>")
    type_opts = ''.join(
        f'<option value="{t}"{" selected" if t == node.type else ""}>{t}</option>' for t in NODE_TYPES)
    current = next((ed.target for ed in fc.edges if ed.source == node.id), "")
    next_opts = '<option value="">— end of flow —</option>' + ''.join(
        f'<option value="{n.id}"{" selected" if n.id == current else ""}>{_esc(n.name)}'
        f'{" ◆ start" if n.id == fc.start else ""}</option>'
        for n in fc.nodes if n.id != node.id)
    note = str(node.config.get("instructions", ""))
    return HTMLResponse(f"""<div class="slide"><div class="tag">Step configuration</div>
      <h3 id="panel-title">{_esc(node.name)}</h3>
      <label for="pf-name">Name</label>
      <input id="pf-name" value="{_esc(node.name)}" maxlength="80">
      <label for="pf-type">Type</label>
      <select id="pf-type">{type_opts}</select>
      <label for="pf-note">What happens at this step?</label>
      <textarea id="pf-note" rows="4" placeholder="e.g. Look up the vendor and check they are approved">{_esc(note)}</textarea>
      <div class="hint">Plain-language description of this step. It is saved with the workflow.</div>
      <label for="pf-next">Connects to (next step)</label>
      <select id="pf-next">{next_opts}</select>
      <div class="hint">The step this one hands over to when it finishes — the same choice as the
      <b>▸</b> selector right on the node.</div>
      <div class="actions panel-actions" style="margin-top:16px">
        <button class="btn" type="button" onclick="saveNode('{node.id}',this)">Apply changes</button>
        <button class="btn reject" type="button" data-noload onclick="deleteNode('{node.id}',this)">Delete step</button>
        <button class="btn btm" type="button" data-noload onclick="closePanel()">Close</button></div>
      <p class="hint">Saving updates the diagram immediately.</p>
      </div>""")


@app.post("/api/flow/{emp_id}/nodes")
async def flow_add_node(emp_id: str, request: Request):
    e, fc = _load_flow(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    form = await request.form()
    name = (form.get("name") or "New step").strip() or "New step"
    ntype = form.get("type") or "finance"
    if ntype not in NODE_TYPES:
        ntype = "finance"
    after = form.get("after") or ""
    src = next((n for n in fc.nodes if n.id == after), None)
    src_has_next = any(ed.source == (src.id if src else "") for ed in fc.edges)
    if src is not None and not src_has_next:
        sx, sy = _node_pos(src, fc.nodes.index(src))
        x, y = sx, sy + 120
    else:
        x, y = _default_pos(len(fc.nodes))
    # nudge until the spot is free
    while any(abs(_node_pos(n, i)[0] - x) < 40 and abs(_node_pos(n, i)[1] - y) < 40
              for i, n in enumerate(fc.nodes)):
        y += 110
    node = FlowNode(id=_new_node_id(fc), type=ntype, name=name,
                    config={"x": round(x), "y": round(y)})
    fc.nodes.append(node)
    if src is not None and not src_has_next:
        fc.edges.append(FlowEdge(source=src.id, target=node.id))
    _save_flow(e)
    log_event("flow_node_added", run_id=e.id, node=node.id, status=ntype)
    return JSONResponse({"flow": _flow_dict(e),
                         "node": {"id": node.id, "type": node.type, "name": node.name}})


@app.post("/api/flow/{emp_id}/nodes/{node_id}")
async def flow_update_node(emp_id: str, node_id: str, request: Request):
    e, fc = _load_flow(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    node = next((n for n in fc.nodes if n.id == node_id), None)
    if not node:
        return JSONResponse({"error": "step not found"}, status_code=404)
    form = await request.form()
    if form.get("name") is not None and form.get("name").strip():
        node.name = form.get("name").strip()[:80]
    if form.get("type") in NODE_TYPES:
        node.type = form.get("type")
    if form.get("instructions") is not None:
        node.config["instructions"] = form.get("instructions")
    if "next" in form:
        nxt = form.get("next") or ""
        fc.edges = [ed for ed in fc.edges if ed.source != node_id]
        if nxt and nxt != node_id and any(n.id == nxt for n in fc.nodes):
            fc.edges.append(FlowEdge(source=node_id, target=nxt))
    _save_flow(e)
    log_event("flow_node_updated", run_id=e.id, node=node.id, status=node.type)
    return JSONResponse({"flow": _flow_dict(e)})


@app.post("/api/flow/{emp_id}/nodes/{node_id}/delete")
def flow_delete_node(emp_id: str, node_id: str):
    e, fc = _load_flow(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    if node_id == fc.start:
        return JSONResponse({"error": "the start step cannot be deleted"}, status_code=400)
    if not any(n.id == node_id for n in fc.nodes):
        return JSONResponse({"error": "step not found"}, status_code=404)
    fc.nodes = [n for n in fc.nodes if n.id != node_id]
    fc.edges = [ed for ed in fc.edges if ed.source != node_id and ed.target != node_id]
    _save_flow(e)
    log_event("flow_node_deleted", run_id=e.id, node=node_id)
    return JSONResponse({"flow": _flow_dict(e)})


@app.post("/api/flow/{emp_id}/positions")
async def flow_positions(emp_id: str, request: Request):
    e, fc = _load_flow(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    pos = (body or {}).get("positions") or {}
    if not isinstance(pos, dict):
        return JSONResponse({"error": "positions must be an object"}, status_code=400)
    for n in fc.nodes:
        p = pos.get(n.id)
        if isinstance(p, dict) and isinstance(p.get("x"), (int, float)) and isinstance(p.get("y"), (int, float)):
            n.config["x"] = round(p["x"])
            n.config["y"] = round(p["y"])
    _save_flow(e)
    return JSONResponse({"ok": True})


def _translate(rec: dict) -> tuple[str, str]:
    tool = rec.get("tool") or ""
    at = rec.get("action_type")
    summary = str(rec.get("summary", "") or "")
    # Approval and clarification records carry their own story — without these
    # branches the approval + the tool result both rendered as the same line
    # ("Checked the payment policy" twice).
    if summary.startswith("Approval requested:"):
        if summary.rstrip().endswith("rejected"):
            return "warn", f"You rejected: {tool}"
        return "ok", f"You approved: {tool}"
    if summary.startswith("Human said:"):
        return "ok", f"You said: {summary.split('Human said:', 1)[1].strip()}"
    if summary.startswith("Workflow advanced"):
        return "ok", summary
    if at == "ask_human":
        return "ok", "Clarification with you"
    if at == "verify":
        return "ok", "Verifying the result against the system"
    if at == "finish":
        return "ok", "Finishing the task"
    mapping = {
        "search_files": "Searched company files", "read_file": "Read a document",
        "list_knowledge": "Listed available knowledge",
        "vendor_lookup": "Looked up vendor master data", "po_lookup": "Matched purchase orders",
        "duplicate_check": "Checked for duplicate invoices", "policy_check": "Checked the payment policy",
        "currency_convert": "Converted currency", "financial_calculator": "Ran a financial calculation",
        "create_payment": "Prepared a payment", "update_vendor_bank": "Updated vendor bank details",
        "generate_report": "Generated a report",
        "browser_navigate": "Opened the payables app", "browser_fill": "Filled a form field",
        "browser_submit": "Submitted the form", "browser_read": "Inspected the page",
    }
    return "ok", mapping.get(tool, f"{at} {tool}".strip())


def _run_page(employee=None, mode: str = "use") -> str:
    _seed()
    employees = _employees()
    if employee is None and employees:
        employee = employees[0]
    if not employees:
        body = ('<div class="section">Run an employee</div>'
                '<div class="empty" style="background:#fff;border:1px solid var(--line);'
                'border-radius:var(--radius)"><div class="big">🤖</div>'
                'No employees yet — create one first, then come back to give it a task.<br><br>'
                '<a class="btn" href="/agents?mode=configure">Create an employee</a></div>')
        return _shell("Run", body, mode)
    options = "".join(
        f'<option value="{e.id}"{" selected" if employee and e.id == employee.id else ""}>{e.name}</option>'
        for e in employees)
    emp_id = employee.id if employee else ""
    name = _esc(employee.name) if employee else "AI Employee"
    role = _esc(employee.role) if employee else "finance"
    suggestions = [
        "Process the latest Acme invoice and prepare payment",
        "Look up vendor Acme Corp",
        "Check policy for a 120000 invoice",
    ]
    chips = "".join(
        f'<button type="button" class="btn btm" data-noload '
        f'onclick="document.getElementById(\'chat-text\').value=this.textContent">'
        f'{_esc(s)}</button>' for s in suggestions)
    body = f"""<div class="row" style="margin-bottom:12px">
      <div class="card" style="flex:2"><label style="margin-top:0">Employee</label>
        <select id="chat-emp" onchange="location.href='/run/'+this.value+'?mode=use'">{options}</select>
        <div class="hint">Pick the employee you want to give work to.</div></div>
      <div class="card" style="display:flex;align-items:center;justify-content:center">
        <a class="btn ghost" href="/agents/{emp_id}?mode=configure">Configure this employee</a></div>
    </div>
    <div class="chat">
      <div class="chat-head"><strong>{name}</strong> <span class="badge status" id="chat-status">ready</span></div>
      <div class="chat-log" id="chat-log"><div class="msg assistant">Hi, I'm your {role} employee.
Tell me a finance task in plain English and I'll do it — I'll ask you when I need approval or a clarification.</div></div>
      <div class="hint" style="margin:0 0 8px">Not sure what to say? Try one of these:</div>
      <div class="actions" style="margin-bottom:12px">{chips}</div>
      <form class="chat-input" onsubmit="return chatSend(event,'{emp_id}')">
        <textarea id="chat-text" placeholder="e.g. Process the latest Acme invoice and prepare payment" required></textarea>
        <button class="btn" type="submit" data-noload>Send</button>
      </form>
    </div>"""
    return _shell("Run", body, mode)


@app.get("/run", response_class=HTMLResponse)
def run_home(mode: str = "use"):
    return _run_page(mode=mode)


@app.get("/run/{emp_id}", response_class=HTMLResponse)
def run_employee(emp_id: str, mode: str = "use"):
    return _run_page(store.get_employee(emp_id), mode)


@app.post("/run/start")
@app.post("/api/run/start")
def run_start(emp_id: str = Form(...), task: str = Form(...)):
    e = store.get_employee(emp_id)
    if not e:
        return JSONResponse({"error": "unknown employee"}, status_code=404)
    run_id = f"{emp_id}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:4]}"
    op = compile_employee(e, APP_BASE_URL, STORAGE_DIR)
    run = {"op": op, "queue": queue.Queue(), "gate": threading.Event(), "answer": None,
           "seen": 0, "done": False, "record": RunRecord(id=run_id, employee_id=e.id,
           employee_name=e.name, task=task, status="running")}
    RUNS[run_id] = run
    store.save_run(run["record"])
    threading.Thread(target=_drive, args=(run, task), daemon=True).start()
    return JSONResponse({"run_id": run_id, "employee": e.name})


def _emit_steps(run: dict, result: dict) -> None:
    records = result.get("records", [])
    for rec in records[run["seen"]:]:
        cls, text = _translate(rec)
        run["queue"].put({"event": "action",
                          "data": {"step": rec.get("step"), "text": text, "tool": rec.get("tool"), "cls": cls}})
    run["seen"] = len(records)


def _drive(run: dict, task: str) -> None:
    op: Operator = run["op"]
    run_id = run["record"].id
    result: dict = {}
    try:
        run["queue"].put({"event": "status", "data": {"status": "working", "cls": "warn"}})
        result = op.start(task, run_id=run_id)
        while True:
            _emit_steps(run, result)
            pending = Operator.suspended(result)
            if not pending:
                break
            p = pending[0]
            run["pending"] = p
            if p.get("kind") == "clarification":
                run["queue"].put({"event": "question", "data": {"html": _question_card(run_id, p)}})
            else:
                run["queue"].put({"event": "approval", "data": {"html": _approval_card(run_id, p)}})
            run["gate"].clear()
            answered = run["gate"].wait(timeout=600)
            run["pending"] = None
            if not answered:
                result = {"report": {"status": "failed",
                                     "summary": "No response to the approval/clarification "
                                                "within 10 minutes — run stopped.",
                                     "actions": []}}
                break
            result = op.resume(run_id, run["answer"])
    except Exception as exc:  # noqa: BLE001
        run["record"].status = "failed"
        msg = str(exc)
        log_event("run_failed", run_id=run_id, level="error",
                  error=f"{type(exc).__name__}: {msg[:300]}")
        if "429" in msg or "rate limit" in msg.lower():
            text = ("The AI model is rate-limiting requests right now — wait a moment "
                    "and send the task again.")
        elif "api key" in msg.lower() or "401" in msg:
            text = "The model provider rejected the API key — check GROQ_API_KEY in .env."
        elif "connect" in msg.lower() or "timeout" in msg.lower() or "timed out" in msg.lower():
            text = "Could not reach the AI model (network) — check your connection and try again."
        else:
            text = f"The run stopped on an error: {type(exc).__name__}: {msg[:200]}"
        run["queue"].put({"event": "assistant", "data": {"text": text}})
    _finish(run, result)


def _approval_card(run_id: str, p: dict) -> str:
    amount = p.get("amount")
    amount_s = f"{amount:,.2f} {p.get('currency') or ''}".strip() if isinstance(amount, (int, float)) else ""
    return (f'<span class="tag">Approval required</span>'
            f'<div>{_esc(str(p.get("description", "")))}</div>'
            f'<div class="muted">{_esc(str(p.get("policy", "")))} {amount_s}</div>'
            f'<div style="margin-top:8px">'
            f'<button class="btn ok" onclick="approve(\'{run_id}\',\'yes\',this)">Approve</button> '
            f'<button class="btn reject" onclick="approve(\'{run_id}\',\'no\',this)">Reject</button></div>')


def _question_card(run_id: str, p: dict) -> str:
    return (f'<span class="tag">Clarification</span>'
            f'<div>{_esc(str(p.get("question", "")))}</div>'
            f'<div style="display:flex;gap:6px;margin-top:8px">'
            f'<input placeholder="Your answer" style="margin:0">'
            f'<button class="btn" onclick="reply(\'{run_id}\',this)">Reply</button></div>')


def _finish(run: dict, result: dict) -> None:
    report = result.get("report") or {}
    run["record"].status = "completed" if report.get("status") in ("verified_complete", "completed") else "failed"
    run["record"].duration = time.time() - run["record"].started
    run["record"].trace = result.get("trace_path")
    run["record"].report = FinalReport(**report) if report else None
    store.save_run(run["record"])
    status = run["record"].status
    run["queue"].put({"event": "assistant",
                      "data": {"text": report.get("summary") or "Run finished."}})
    run["queue"].put({"event": "status",
                      "data": {"status": status, "cls": "ok" if status == "completed" else "bad"}})
    run["queue"].put({"event": "__done__", "data": {}})
    run["done"] = True


@app.get("/run/stream/{run_id}")
def run_stream(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        return JSONResponse({"error": "unknown run"}, status_code=404)

    def gen():
        while True:
            try:
                item = run["queue"].get(timeout=30)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            if item["event"] == "__done__":
                yield "event: done\ndata: {}\n\n"
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/run/approve/{run_id}")
def run_approve(run_id: str, decision: str = "yes"):
    run = RUNS.get(run_id)
    if not run:
        return JSONResponse({"error": "unknown run"}, status_code=404)
    run["answer"] = "yes" if decision == "yes" else "no"
    run["gate"].set()
    return JSONResponse({"ok": True})


@app.post("/run/reply/{run_id}")
def run_reply(run_id: str, answer: str = "ok"):
    run = RUNS.get(run_id)
    if not run:
        return JSONResponse({"error": "unknown run"}, status_code=404)
    run["answer"] = answer
    run["gate"].set()
    return JSONResponse({"ok": True})


_STATUS_BADGE = {"completed": "ok", "failed": "bad", "running": "warn",
                 "waiting_approval": "warn", "interrupted": "warn"}


@app.get("/history", response_class=HTMLResponse)
def history(mode: str = "use"):
    rows = ""
    for r in store.list_runs():
        rows += (f'<tr><td>{_esc(r.employee_name)}</td><td>{_esc(r.task[:48])}</td>'
                 f'<td><span class="badge {_STATUS_BADGE.get(r.status, "")}">'
                 f'{_esc(STATUS_LABELS.get(r.status, r.status))}</span></td>'
                 f'<td class="tag">{time.strftime("%H:%M:%S", time.localtime(r.started))}</td>'
                 f'<td class="tag">{(r.duration or 0):.2f}s</td>'
                 f'<td><a class="btn ghost" href="/history/{r.id}">View trace</a></td></tr>')
    body = f"""<div class="section">Activity history</div>
      <p class="hint">Every task you have given to an employee. Click <b>View trace</b> to see
      exactly what it did, and the report it produced.</p>
      <div class="card" style="margin-top:12px"><table>
      <tr><th>Employee</th><th>Task</th><th>Status</th><th>Started</th><th>Duration</th><th></th></tr>
      {rows or '<tr><td colspan="6" class="muted">Nothing here yet — run an employee to see activity.</td></tr>'}</table></div>"""
    return _shell("History", body, mode)


@app.get("/history/{run_id}", response_class=HTMLResponse)
def history_detail(run_id: str):
    r = store.get_run(run_id)
    if not r:
        return RedirectResponse("/history")
    trace = ""
    if r.trace and os.path.isfile(r.trace):
        with open(r.trace, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "step" in rec:
                    trace += (f'<li><span class="tag">step {_esc(str(rec["step"]))}</span> '
                              f'{_esc(str(rec.get("summary", ""))[:200])}</li>')
    rd = _report_dict(r.report)
    if rd:
        ver = rd.get("verification") or {}
        acts = "".join(f"<li>{_esc(str(a))}</li>" for a in (rd.get("actions") or [])[:24])
        match = ver.get("matched")
        ver_line = ""
        if ver:
            ver_line = (f'<p>Verifier: expected {_esc(str(ver.get("expected", "")))} · '
                        f'found {_esc(str(ver.get("found", "")))} · '
                        f'<span class="badge {"ok" if match else "bad"}">'
                        f'{"matched" if match else "mismatch"}</span></p>')
        report = (f'<p><b>{_esc(str(rd.get("summary", "")))}</b></p>{ver_line}'
                  f'<h4>Actions</h4><ul>{acts or "<li class=muted>None listed.</li>"}</ul>'
                  f'<details><summary class="muted">Technical report</summary>'
                  f'<pre>{_esc(json.dumps(rd, indent=2, default=str))}</pre></details>')
    else:
        report = '<p class="muted">No report was produced for this run.</p>'
    body = f"""<p class="hint"><a href="/history">← Activity history</a></p>
      <div class="card">
      <div class="kv"><h3 style="margin:0">{_esc(r.employee_name)}</h3>
        <span class="badge {_STATUS_BADGE.get(r.status, "")}">{_esc(STATUS_LABELS.get(r.status, r.status))}</span></div>
      <p class="muted">Task: {_esc(r.task)}</p>
      <p class="tag">Started {time.strftime("%H:%M:%S", time.localtime(r.started))} · took {(r.duration or 0):.2f}s</p>
      <h4>What it did</h4>
      <ul class="timeline">{trace or '<li class="muted">No steps recorded.</li>'}</ul>
      <h4>Report</h4>{report}
      <div class="actions">
        <a class="btn ghost" href="/history">← Back to history</a>
        <a class="btn ghost" href="/logs?run_id={_esc(r.id)}">View logs</a></div></div>"""
    return _shell("History", body, "use")


# --------------------------------------------------------------------------- #
# logs
# --------------------------------------------------------------------------- #

_LOG_KEYS = ("step", "tool", "action_type", "ok", "status", "matched", "kind", "amount",
             "approved", "vendor", "due_date", "question", "answer", "provider", "model")
_LOG_BAD = {"model_error", "invalid_model_output", "tool_failed_after_retries"}


def _event_detail(e: dict) -> str:
    bits = [f"{k}={e[k]}" for k in _LOG_KEYS if e.get(k) not in (None, "")]
    return _esc("  ".join(bits)) if bits else "<span class='muted'>—</span>"


@app.get("/logs", response_class=HTMLResponse)
def logs(mode: str = "use", run_id: str = ""):
    events = read_events(limit=300, run_id=run_id or None)
    rows = ""
    for e in reversed(events):
        ts = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
        name = str(e.get("event", ""))
        cls = "bad" if name in _LOG_BAD else "ok" if name == "final_report" else ""
        rows += (f'<tr><td class="tag">{ts}</td>'
                 f'<td><span class="badge {cls}">{_esc(name)}</span></td>'
                 f'<td class="tag">{_esc(str(e.get("run_id", "") or ""))[:30]}</td>'
                 f'<td>{_event_detail(e)}</td></tr>')
    note = f" — run {_esc(run_id)}" if run_id else ""
    body = f"""<div class="card"><h3>Logs{note}</h3>
      <p class="muted">Newest first · every model call, tool, approval and verifier
      result · <code>{_esc(os.path.join(STORAGE_DIR, 'logs', 'traces.jsonl'))}</code></p>
      <table><tr><th>time</th><th>event</th><th>run</th><th>detail</th></tr>
      {rows or '<tr><td colspan="4" class="muted">No events logged yet.</td></tr>'}</table>
      <div style="margin-top:12px"><a class="btn ghost" href="/logs">Refresh</a></div></div>"""
    return _shell("Logs", body, mode)


@app.get("/api/logs", response_class=JSONResponse)
def api_logs(run_id: str = "", limit: int = 200):
    return JSONResponse(read_events(limit=limit, run_id=run_id or None))


def main() -> None:
    """Start the dashboard on the configured port (env DASHBOARD_PORT or 8080)."""
    import uvicorn
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    uvicorn.run("builder.dashboard:app", host="127.0.0.1", port=port, reload=False)