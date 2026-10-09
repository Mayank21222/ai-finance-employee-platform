"""Sandbox payables web app (FastAPI + SQLite). Fails are injected on
purpose so the agent must observe and recover. Runs on APP_BASE_URL.

Besides the invoice form (driven by the browser tool) this exposes the
finance data an AI finance employee works with: vendors, purchase orders
and payments. Reads are GET/JSON; writes are explicit endpoints the
guarded tools call."""
from __future__ import annotations

import os
import sqlite3
import time
from urllib.parse import unquote

from fastapi import Body, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ai_operator.observability import configure_logging, log_event

DB_PATH = os.path.join(os.getenv("STORAGE_DIR", ".data"), "payables.db")
configure_logging(os.getenv("STORAGE_DIR", ".data"))

app = FastAPI(title="Payables (sandbox)")

_SEED_VENDORS = [
    ("Acme Corp", "27AAACA1234A1Z5", "HDFC ****1234", "NET30", "active", "ap@acme.example"),
    ("Globex Inc", "27AAACB5678B1Z2", "ICICI ****7788", "NET15", "active", "billing@globex.example"),
    ("Initech", "27AAACC9012C1Z9", "SBI ****4455", "NET45", "on_hold", "finance@initech.example"),
]

_SEED_POS = [
    ("PO-1001", "Acme Corp", 42500.0, "INR", "open"),
    ("PO-1002", "Globex Inc", 95000.0, "INR", "open"),
    ("PO-1003", "Acme Corp", 100000.0, "INR", "open"),
    ("PO-1004", "Initech", 7000.0, "INR", "closed"),
]


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_number TEXT, vendor TEXT, amount REAL, due_date TEXT, created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS vendors(
        name TEXT PRIMARY KEY, tax_id TEXT, bank TEXT, terms TEXT, status TEXT, email TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchase_orders(
        po_number TEXT PRIMARY KEY, vendor TEXT, amount REAL, currency TEXT, status TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT, amount REAL, currency TEXT,
        due_date TEXT, status TEXT, created_at TEXT)""")
    for v in _SEED_VENDORS:
        c.execute("INSERT OR IGNORE INTO vendors(name,tax_id,bank,terms,status,email) VALUES(?,?,?,?,?,?)", v)
    for p in _SEED_POS:
        c.execute("INSERT OR IGNORE INTO purchase_orders(po_number,vendor,amount,currency,status) VALUES(?,?,?,?,?)", p)
    return c


_PAGE_STYLE = "<style>body{font-family:system-ui,sans-serif;margin:2rem}\
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.4rem}\
.popup-overlay{position:fixed;inset:0;background:#eee;z-index:9}\
input{display:block;margin:.4rem 0}button{margin-top:.6rem}</style>"


@app.get("/", response_class=HTMLResponse)
def home():
    return f"""<html><head>{_PAGE_STYLE}</head><body>
<h1>Payables</h1><a href="/invoices">Invoices</a> | <a href="/invoice/new">Add invoice</a>
<p>Sandbox only.</p></body></html>"""


@app.get("/invoice/new", response_class=HTMLResponse)
def invoice_new(fail: str = ""):
    if fail == "slow":
        time.sleep(3)
    popup = '<div class="popup-overlay">Unrelated modal in the way</div>' if fail == "popup" else ""
    date_name = "due-date-field" if fail == "rename" else "due_date"
    return f"""<html><head>{_PAGE_STYLE}</head><body>
{popup}
<h1>Add invoice</h1>
<form action="/invoice/new" method="post">
Invoice#:<input name="invoice_number" type="text">
Vendor:<input name="vendor" type="text">
Amount (INR):<input name="amount" type="text">
Due date:<input name="{date_name}" type="text" placeholder="YYYY-MM-DD">
<button type="submit">Submit</button>
</form><a href="/">Back</a></body></html>"""


@app.post("/invoice/new")
def invoice_create(request: Request,
                   invoice_number: str = Form(""), vendor: str = Form(""),
                   amount: str = Form(""), due_date: str = Form(""),
                   due_date_alt: str = Form("", alias="due-date-field")):
    due_date = due_date or due_date_alt
    try:
        amt = float(amount.replace(",", ""))
        _ = time.strptime(due_date, "%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        return HTMLResponse(f"<html><body><h1>Validation error</h1><p>Bad amount or"
                            f" due date ({exc})</p><a href='/invoice/new'>Back</a></body></html>", status_code=400)
    with _conn() as c:
        c.execute("INSERT INTO invoices(invoice_number,vendor,amount,due_date,created_at) VALUES(?,?,?,?,?)",
                  (invoice_number or f"INV-{int(time.time())}", vendor, amt, due_date, time.strftime("%Y-%m-%d %H:%M:%S")))
    log_event("sandbox_invoice_created", vendor=vendor, amount=amt, due_date=due_date)
    return RedirectResponse("/invoices", status_code=303)


@app.get("/invoices", response_class=HTMLResponse)
def invoices_list():
    with _conn() as c:
        rows = c.execute("SELECT id,invoice_number,vendor,amount,due_date FROM invoices ORDER BY id DESC").fetchall()
    body = "".join(
        f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]:,.2f}</td><td>{r[4]}</td></tr>" for r in rows)
    return f"""<html><head>{_PAGE_STYLE}</head><body>
<h1>Invoices</h1><table><tr><th>id</th><th>num</th><th>vendor</th><th>amount</th><th>due</th></tr>{body}</table>
<a href="/invoice/new">Add invoice</a></body></html>"""


@app.get("/api/invoices", response_class=JSONResponse)
def api_invoices(vendor: str | None = None):
    """Read-only path used by the verifier. Never accepts writes."""
    with _conn() as c:
        if vendor:
            rows = c.execute("SELECT id,invoice_number,vendor,amount,due_date FROM invoices WHERE lower(vendor)=lower(?)",
                             (vendor,)).fetchall()
        else:
            rows = c.execute("SELECT id,invoice_number,vendor,amount,due_date FROM invoices").fetchall()
    return [{"id": r[0], "invoice_number": r[1], "vendor": r[2], "amount": r[3], "due_date": r[4]} for r in rows]


# --- finance master data (read) -------------------------------------------- #

@app.get("/api/vendors", response_class=JSONResponse)
def api_vendors(name: str | None = None):
    with _conn() as c:
        if name:
            rows = c.execute("SELECT name,tax_id,bank,terms,status,email FROM vendors "
                             "WHERE lower(name) LIKE lower(?)", (f"%{name}%",)).fetchall()
        else:
            rows = c.execute("SELECT name,tax_id,bank,terms,status,email FROM vendors").fetchall()
    keys = ("name", "tax_id", "bank", "terms", "status", "email")
    return [dict(zip(keys, r)) for r in rows]


@app.get("/api/vendors/{name}", response_class=JSONResponse)
def api_vendor(name: str):
    name = unquote(name)
    with _conn() as c:
        row = c.execute("SELECT name,tax_id,bank,terms,status,email FROM vendors WHERE lower(name)=lower(?)",
                        (name,)).fetchone()
    if not row:
        return JSONResponse({"error": f"vendor '{name}' not found"}, status_code=404)
    keys = ("name", "tax_id", "bank", "terms", "status", "email")
    return dict(zip(keys, row))


@app.get("/api/purchase_orders", response_class=JSONResponse)
def api_purchase_orders(vendor: str | None = None):
    with _conn() as c:
        if vendor:
            rows = c.execute("SELECT po_number,vendor,amount,currency,status FROM purchase_orders "
                             "WHERE lower(vendor)=lower(?)", (vendor,)).fetchall()
        else:
            rows = c.execute("SELECT po_number,vendor,amount,currency,status FROM purchase_orders").fetchall()
    return [{"po_number": r[0], "vendor": r[1], "amount": r[2], "currency": r[3], "status": r[4]} for r in rows]


@app.get("/api/payments", response_class=JSONResponse)
def api_payments(vendor: str | None = None):
    with _conn() as c:
        if vendor:
            rows = c.execute("SELECT id,vendor,amount,currency,due_date,status FROM payments "
                             "WHERE lower(vendor)=lower(?)", (vendor,)).fetchall()
        else:
            rows = c.execute("SELECT id,vendor,amount,currency,due_date,status FROM payments").fetchall()
    return [{"id": r[0], "vendor": r[1], "amount": r[2], "currency": r[3], "due_date": r[4], "status": r[5]}
            for r in rows]


# --- finance writes --------------------------------------------------------- #

@app.post("/api/payments", response_class=JSONResponse)
def api_create_payment(payload: dict = Body(...)):
    vendor = str(payload.get("vendor", "")).strip()
    try:
        amount = float(str(payload.get("amount", "")).replace(",", ""))
        due_date = str(payload.get("due_date", "")).strip()
        _ = time.strptime(due_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return JSONResponse({"ok": False, "error": "amount or due_date invalid"}, status_code=400)
    if not vendor:
        return JSONResponse({"ok": False, "error": "vendor required"}, status_code=400)
    with _conn() as c:
        cur = c.execute("INSERT INTO payments(vendor,amount,currency,due_date,status,created_at) VALUES(?,?,?,?,?,?)",
                        (vendor, amount, str(payload.get("currency", "INR")), due_date, "prepared",
                         time.strftime("%Y-%m-%d %H:%M:%S")))
    log_event("sandbox_payment_created", vendor=vendor, amount=amount, due_date=due_date, id=cur.lastrowid)
    return {"ok": True, "id": cur.lastrowid, "vendor": vendor, "amount": amount, "due_date": due_date}


@app.post("/api/vendors/{name}/bank", response_class=JSONResponse)
def api_update_bank(name: str, payload: dict = Body(...)):
    name = unquote(name)
    bank = str(payload.get("bank_account", "")).strip()
    if not bank:
        return JSONResponse({"ok": False, "error": "bank_account required"}, status_code=400)
    with _conn() as c:
        cur = c.execute("UPDATE vendors SET bank=? WHERE lower(name)=lower(?)", (bank, name))
        if cur.rowcount == 0:
            return JSONResponse({"ok": False, "error": f"vendor '{name}' not found"}, status_code=404)
    log_event("sandbox_vendor_bank_updated", vendor=name, bank_account=bank, level="warning")
    return {"ok": True, "vendor": name, "bank_account": bank}


@app.post("/api/reset")
def api_reset():
    with _conn() as c:
        c.execute("DROP TABLE IF EXISTS invoices")
        c.execute("DROP TABLE IF EXISTS payments")
    return JSONResponse({"ok": True})


def clear_invoices() -> None:
    with _conn() as c:
        c.execute("DROP TABLE IF EXISTS invoices")
        c.execute("""CREATE TABLE IF NOT EXISTS invoices(
            id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT, vendor TEXT,
            amount REAL, due_date TEXT, created_at TEXT)""")
