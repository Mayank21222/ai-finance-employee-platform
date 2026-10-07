"""FastAPI payables mock app: HTML form, record list, read-only API, failure switches."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from mock_app import db, failures

app = FastAPI(title="Mock Payables App")


def build_hash() -> str:
    """Short hash of this module's source; lets run.py detect a stale server."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "build_hash": build_hash()})


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><title>{title}</title>
<style>body{{font-family:sans-serif;margin:2rem;max-width:40rem}}
label{{display:block;margin:.6rem 0 .2rem}} input{{padding:.3rem;width:100%}}
.error{{color:#b00020}} nav a{{margin-right:1rem}}</style></head>
<body><nav><a href="/">Invoices</a><a href="/add">Add invoice</a></nav>
<h1>{title}</h1>{body}</body></html>"""


def _popup() -> str:
    if not failures.is_on("popup"):
        return ""
    return """<div id="popup-overlay" style="position:fixed;inset:0;background:rgba(0,0,0,.6);
z-index:10;display:flex;align-items:center;justify-content:center">
<div id="popup-box" style="background:#fff;padding:1.5rem;border-radius:8px;max-width:20rem">
<p>Unsaved changes will be lost.</p>
<button id="close-popup" onclick="document.getElementById('popup-overlay').style.display='none'">Continue</button></div></div>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    rows = db.list_invoices()
    if not rows:
        items = "<p>No invoices recorded yet.</p>"
    else:
        items = (
            "<table border=1 cellpadding=6><tr><th>id</th><th>vendor</th>"
            "<th>amount</th><th>due_date</th></tr>"
            + "".join(
                f"<tr><td>{r['id']}</td><td>{r['vendor']}</td>"
                f"<td>{r['amount']:.2f}</td><td>{r['due_date']}</td></tr>"
                for r in rows
            )
            + "</table>"
        )
    return _page("Payables — recorded invoices", items)


@app.get("/add", response_class=HTMLResponse)
def add_form(request: Request) -> str:
    failures.maybe_slow()
    error = request.query_params.get("error")
    due_name = "due-date-field" if failures.is_on("renamed_field") else "due_date"
    due_id = "due-date-field" if failures.is_on("renamed_field") else "due_date"
    err_html = f'<p class="error">{error}</p>' if error else ""
    body = f"""{err_html}{_popup()}
<form method="post" action="/invoices">
<label for="vendor">Vendor</label>
<input id="vendor" name="vendor" required>
<label for="amount">Amount (INR)</label>
<input id="amount" name="amount" placeholder="42500.00" required>
<label for="{due_id}">Due date</label>
<input id="{due_id}" name="{due_name}" placeholder="2026-07-30" required>
<button type="submit">Submit invoice</button>
</form>"""
    return _page("Add invoice", body)


@app.post("/invoices")
def create_invoice(
    vendor: str = Form(...),
    amount: str = Form(...),
    due_date: str = Form(default=""),
    due_date_renamed: str = Form(default="", alias="due-date-field"),
):
    failures.maybe_slow()
    due = (due_date or due_date_renamed).strip()
    if not due:
        return RedirectResponse("/add?error=Due date is required", status_code=303)
    cleaned = amount.strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    if failures.is_on("validation") and cleaned != amount.strip():
        return RedirectResponse(
            f"/add?error=Amount must be a plain number, e.g. 42500.00 (got: {amount})",
            status_code=303,
        )
    try:
        value = float(cleaned)
    except ValueError:
        return RedirectResponse(
            f"/add?error=Amount must be a number (got: {amount})", status_code=303
        )
    if value <= 0:
        return RedirectResponse("/add?error=Amount must be greater than zero", status_code=303)
    invoice_id = db.add_invoice(vendor.strip(), value, due)
    return RedirectResponse(f"/?saved={invoice_id}", status_code=303)


@app.get("/api/invoices")
def api_invoices(vendor: str | None = None) -> JSONResponse:
    """Read-only endpoint used by the deterministic verifier."""
    return JSONResponse(db.list_invoices(vendor))


@app.post("/debug/failures")
async def set_failures(request: Request) -> JSONResponse:
    payload = await request.json()
    try:
        modes = failures.set_modes(payload.get("modes", []), replace=payload.get("replace", True))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"active": modes})


@app.post("/debug/reset")
def reset_failures() -> JSONResponse:
    return JSONResponse({"active": failures.clear()})


@app.get("/debug/state")
def debug_state() -> JSONResponse:
    return JSONResponse({"failures": failures.active()})
