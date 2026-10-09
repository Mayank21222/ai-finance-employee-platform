"""Tool registry and sandbox tools: files, memory, and an HTTP-driven browser.

The 'browser' is deliberately a deterministic HTTP form client against the
sandbox mock app (no real browser, no network beyond localhost). It supports
navigate / read / fill / submit / screenshot so the agent can recover from
renamed fields, popups and validation errors.
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable

import httpx

from ai_operator.models import Permission, ToolSpec

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8001")
DATA_DIR = os.getenv("DATA_DIR", "company_data")
STORAGE_DIR = os.getenv("STORAGE_DIR", ".data")


class FormParser(HTMLParser):
    """Collect the first <form> plus its input field names and texts."""

    def __init__(self) -> None:
        super().__init__()
        self.action = ""
        self.method = "post"
        self.fields: list[str] = []
        self.text: list[str] = []
        self._in_form = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and not self.action:
            self._in_form = True
            self.action = a.get("action", "")
            self.method = (a.get("method", "post") or "post").lower()
        if self._in_form and tag in ("input", "select", "textarea"):
            name = a.get("name")
            if name:
                self.fields.append(name)

    def handle_data(self, data: str) -> None:
        if self._in_form:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            self._in_form = False


class BrowserSession:
    """Stateful, deterministic browser against the sandbox app."""

    def __init__(self, base_url: str = APP_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.url = ""
        self.body = ""
        self.fields: list[str] = []
        self.values: dict[str, str] = {}
        self.action = ""
        self.method = "post"
        self._client = httpx.Client(base_url=self.base_url, timeout=15, follow_redirects=True)

    def navigate(self, path: str) -> str:
        self.url = path if path.startswith("http") else f"{self.base_url}{path}"
        r = self._client.get(self.url)
        r.raise_for_status()
        self._absorb(r)
        return f"Loaded {self.url}\n{self._summary()}"

    def _absorb(self, r: httpx.Response) -> None:
        self.body = r.text
        p = FormParser()
        p.feed(r.text)
        self.fields = p.fields
        self.values = {f: "" for f in self.fields}
        self.action = p.action
        self.method = p.method

    def _summary(self) -> str:
        return f"Available fields: {', '.join(self.fields) or 'none'}"

    def read(self) -> str:
        return f"Current page: {self.url}\nAvailable fields: {', '.join(self.fields) or 'none'}"

    def fill(self, field: str, value: str) -> str:
        if field not in self.values:
            raise ToolError(f"element '{field}' not found; available inputs: {', '.join(self.fields) or 'none'}")
        self.values[field] = value
        return f"Filled {field}"

    def submit(self) -> str:
        if not self.action:
            raise ToolError("no form on current page")
        url = self.action if self.action.startswith("http") else f"{self.base_url}{self.action}"
        payload = {k: v for k, v in self.values.items() if v != ""}
        r = self._client.post(url, data=payload)
        if r.status_code >= 400:
            return f"Form submission failed ({r.status_code}). The page reported: {self._text_only(r.text)[:300]}"
        self._absorb(r)
        return f"Submitted form -> {r.status_code}"

    @staticmethod
    def _text_only(body: str) -> str:
        return " ".join(body.replace("<", " <").split())

    def screenshot(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.body)
        return path

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


class ToolError(Exception):
    pass


@dataclass
class ToolContext:
    """Per-run state handed to tools and the verifier."""

    base_url: str = APP_BASE_URL
    data_dir: str = DATA_DIR
    storage_dir: str = STORAGE_DIR
    memory: dict[str, str] = field(default_factory=dict)
    browser: BrowserSession = field(init=False)

    def __post_init__(self) -> None:
        self.browser = BrowserSession(self.base_url)

    def memory_path(self) -> str:
        os.makedirs(self.storage_dir, exist_ok=True)
        return os.path.join(self.storage_dir, "memory.json")

    def load_memory(self) -> dict[str, str]:
        try:
            with open(self.memory_path(), encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_memory(self) -> None:
        os.makedirs(self.storage_dir, exist_ok=True)
        with open(self.memory_path(), "w", encoding="utf-8") as fh:
            json.dump(self.memory, fh, indent=2)


@dataclass
class Tool:
    spec: ToolSpec
    fn: Callable[..., str]

    def run(self, ctx: ToolContext, **args: Any) -> str:
        return self.fn(ctx, **args)


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #


def _safe_path(ctx: ToolContext, path: str) -> str:
    root = os.path.abspath(ctx.data_dir)
    candidates: list[str] = []
    if os.path.isabs(path):
        candidates.append(os.path.abspath(path))
    else:
        candidates.append(os.path.abspath(path))                               # relative to cwd
        candidates.append(os.path.abspath(os.path.join(root, path)))            # relative to data dir
        candidates.append(os.path.abspath(os.path.join(root, os.path.basename(path))))
    inside = [c for c in candidates if c == root or c.startswith(root + os.sep)]
    for c in inside:
        if os.path.exists(c):
            return c
    if inside:
        return inside[0]
    raise ToolError(f"path outside data dir: {path}")


def t_search_files(ctx: ToolContext, query: str, directory: str = ".") -> str:
    """Keyword search over file names and contents.

    The query is tokenized and ALL terms must match (name OR content per term),
    so natural queries like "Acme invoice" find Acme_INV-003.txt instead of
    failing as a single literal substring.
    """
    root = os.path.join(ctx.data_dir, directory.lstrip("/"))
    terms = [t for t in query.lower().split() if t] or [query.lower()]
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            haystack_name = name.lower()
            try:
                with open(full, encoding="utf-8", errors="ignore") as fh:
                    content = fh.read().lower()
            except OSError:
                content = ""
            if all(t in haystack_name or t in content for t in terms):
                hits.append(full)
    if hits:
        return "\n".join(hits)
    return (f"No files matching '{query}' found under {root}. "
            f"Try a shorter query (fewer words) or call list_knowledge to see "
            f"what files exist — do not repeat the identical search.")


def t_read_file(ctx: ToolContext, path: str) -> str:
    full = _safe_path(ctx, path)
    with open(full, encoding="utf-8") as fh:
        return fh.read()


def t_memory_read(ctx: ToolContext, key: str) -> str:
    return str(ctx.load_memory().get(key, ""))


def t_memory_write(ctx: ToolContext, key: str, value: str) -> str:
    ctx.memory = ctx.load_memory()
    ctx.memory[key] = value
    ctx.save_memory()
    return f"Saved memory {key}"


def t_browser_navigate(ctx: ToolContext, url: str) -> str:
    if ctx.browser.body and "popup-overlay" in ctx.browser.body:
        pass  # a popup gets noted in the summary; the form stays usable
    return ctx.browser.navigate(url)


def t_browser_read(ctx: ToolContext) -> str:
    return ctx.browser.read()


def t_browser_fill(ctx: ToolContext, field: str, value: str) -> str:
    return ctx.browser.fill(field, value)


def t_browser_submit(ctx: ToolContext) -> str:
    return ctx.browser.submit()


def t_browser_screenshot(ctx: ToolContext, path: str) -> str:
    full = os.path.abspath(path)
    return ctx.browser.screenshot(full)


def t_app_get(ctx: ToolContext, path: str) -> str:
    r = httpx.get(f"{ctx.browser.base_url}{path}", timeout=10)
    r.raise_for_status()
    return r.text


def t_list_knowledge(ctx: ToolContext) -> str:
    names = []
    for base in (ctx.data_dir,):
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                names.append(os.path.relpath(os.path.join(dirpath, name), base))
    return "\n".join(sorted(names)) or "no knowledge loaded"


# --------------------------------------------------------------------------- #
# Finance-domain tools: the actions that an LLM alone cannot do.
# Deterministic code performs the lookup / calculation / write; the model
# only chooses when to call them.
# --------------------------------------------------------------------------- #

_CURRENCY_RATES_INR = {"INR": 1.0, "USD": 0.012, "EUR": 0.011, "GBP": 0.0095}

_CALC_ALLOWED = (
    ast.Expression, ast.Constant, ast.BinOp, ast.UnaryOp, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
)


def _get_json(ctx: ToolContext, path: str, params: dict | None = None):
    r = httpx.get(f"{ctx.browser.base_url}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _money(value: Any) -> float:
    cleaned = str(value).replace(",", "").replace("₹", "").replace("INR", "").strip()
    return float(cleaned)


def t_vendor_lookup(ctx: ToolContext, vendor: str) -> str:
    data = _get_json(ctx, "/api/vendors", {"name": vendor})
    if not data:
        known = ", ".join(v["name"] for v in _get_json(ctx, "/api/vendors"))
        return f"No vendor matching '{vendor}'. Known vendors: {known}"
    v = data[0]
    return (f"Vendor {v['name']}: status={v['status']}, terms={v['terms']}, "
            f"bank={v['bank']}, tax_id={v['tax_id']}, email={v['email']}")


def t_po_lookup(ctx: ToolContext, vendor: str, amount: Any = None) -> str:
    rows = _get_json(ctx, "/api/purchase_orders", {"vendor": vendor})
    if not rows:
        return f"No purchase orders found for {vendor}"
    lines = [f"{r['po_number']}: {r['amount']:,.2f} {r['currency']} ({r['status']})" for r in rows]
    best = ""
    if amount is not None:
        try:
            amt = _money(amount)
        except ValueError:
            amt = None
        if amt is not None:
            candidate = min(rows, key=lambda r: abs(float(r["amount"]) - amt))
            best = f" Closest match: {candidate['po_number']} ({candidate['amount']:,.2f}, diff {abs(candidate['amount']-amt):,.2f})."
    return f"Purchase orders for {vendor}: " + "; ".join(lines) + "." + best


def t_duplicate_check(ctx: ToolContext, vendor: str, amount: Any, due_date: str = "") -> str:
    rows = _get_json(ctx, "/api/invoices", {"vendor": vendor})
    try:
        amt = _money(amount)
    except ValueError:
        raise ToolError(f"amount '{amount}' is not a number")
    matches = [r for r in rows if float(r["amount"]) == amt and (not due_date or r.get("due_date") == due_date)]
    if matches:
        detail = "; ".join(f"{r.get('invoice_number')} id={r.get('id')} {r['amount']} due {r.get('due_date')}"
                            for r in matches)
        return f"DUPLICATE suspected for {vendor} {amt:,.2f}: {detail}"
    return f"No duplicate found for {vendor} {amt:,.2f} ({len(rows)} existing invoice(s) checked)."


def t_policy_check(ctx: ToolContext, amount: Any, category: str = "") -> str:
    from ai_operator.policy import default_policy

    try:
        amt = _money(amount)
    except ValueError:
        raise ToolError(f"amount '{amount}' is not a number")
    policy = default_policy()
    rule = policy.rule_for(amt)
    who = {"auto": "no human approval", "manager_approval": "manager approval",
           "finance_approval": "finance approval"}.get(rule.action, rule.action)
    label = f" [{category}]" if category else ""
    return (f"Policy check{label}: {amt:,.2f} {policy.currency} -> {rule.action} "
            f"({who}). Human approval required: {policy.requires_approval(amt)}.")


def t_currency_convert(ctx: ToolContext, amount: Any, frm: str = "INR", to: str = "USD") -> str:
    frm, to = frm.upper(), to.upper()
    if frm not in _CURRENCY_RATES_INR or to not in _CURRENCY_RATES_INR:
        raise ToolError(f"unsupported currency pair {frm}/{to}; known: {', '.join(_CURRENCY_RATES_INR)}")
    try:
        amt = _money(amount)
    except ValueError:
        raise ToolError(f"amount '{amount}' is not a number")
    inr = amt / _CURRENCY_RATES_INR[frm]
    converted = inr * _CURRENCY_RATES_INR[to]
    return f"{amt:,.2f} {frm} = {converted:,.2f} {to} (rate {_CURRENCY_RATES_INR[to] / _CURRENCY_RATES_INR[frm]:.6f})"


def _safe_eval(expression: str) -> float:
    node = ast.parse(expression, mode="eval")
    for sub in ast.walk(node):
        if not isinstance(sub, _CALC_ALLOWED):
            raise ToolError(f"unsupported expression element: {type(sub).__name__}")
    return eval(compile(node, "<calculator>", "eval"), {"__builtins__": {}}, {})


def t_financial_calculator(ctx: ToolContext, expression: str) -> str:
    value = _safe_eval(expression)
    return f"{expression} = {value}"


def t_create_payment(ctx: ToolContext, vendor: str, amount: Any, due_date: str,
                     currency: str = "INR") -> str:
    payload = {"vendor": vendor, "amount": amount, "due_date": due_date, "currency": currency}
    r = httpx.post(f"{ctx.browser.base_url}/api/payments", json=payload, timeout=10)
    if r.status_code >= 400:
        return f"Payment request rejected ({r.status_code}): {r.text[:200]}"
    body = r.json()
    return f"Payment prepared: {body.get('vendor')} {body.get('amount'):,.2f} {currency} due {body.get('due_date')} (id {body.get('id')})"


def t_update_vendor_bank(ctx: ToolContext, vendor: str, bank_account: str) -> str:
    r = httpx.post(f"{ctx.browser.base_url}/api/vendors/{vendor}/bank",
                   json={"bank_account": bank_account}, timeout=10)
    if r.status_code >= 400:
        return f"Bank update rejected ({r.status_code}): {r.text[:200]}"
    return f"Bank account for {vendor} updated to {bank_account}"


def t_generate_report(ctx: ToolContext, title: str, content: str) -> str:
    out_dir = os.path.join(ctx.storage_dir, "reports")
    os.makedirs(out_dir, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "report"
    path = os.path.join(out_dir, f"{slug}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {title}\n\n{content}\n")
    return f"Report written: {path}"


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def _tool(name: str, description: str, perm: Permission, risk: str, fn: Callable[..., str],
          inputs: str = "", outputs: str = "") -> Tool:
    return Tool(
        spec=ToolSpec(
            name=name, description=description, inputs=inputs, outputs=outputs,
            permission=perm, risk_level=risk, requires_approval=(perm == Permission.IRREVERSIBLE_WRITE),
        ),
        fn=fn,
    )


TOOLS: dict[str, Tool] = {
    "search_files": _tool(
        "search_files", "Find knowledge/invoice files by keyword under company_data.", Permission.READ, "low",
        t_search_files, "query:str, directory?:str", "file paths"),
    "read_file": _tool(
        "read_file", "Read a text file from company_data.", Permission.READ, "low",
        t_read_file, "path:str", "file text"),
    "list_knowledge": _tool(
        "list_knowledge", "List all loaded knowledge/invoice files.", Permission.READ, "low",
        t_list_knowledge, "", "file paths"),
    "memory_read": _tool(
        "memory_read", "Read a value from the persistent company memory.", Permission.READ, "low",
        t_memory_read, "key:str", "value"),
    "memory_write": _tool(
        "memory_write", "Write a value to the persistent company memory.", Permission.REVERSIBLE_WRITE, "medium",
        t_memory_write, "key:str, value:str", "confirmation"),
    "browser_navigate": _tool(
        "browser_navigate", "Open a page in the sandbox app.", Permission.READ, "low",
        t_browser_navigate, "url:str", "page summary + form fields"),
    "browser_read": _tool(
        "browser_read", "Read the current page's form fields.", Permission.READ, "low",
        t_browser_read, "", "available field names"),
    "browser_fill": _tool(
        "browser_fill", "Fill a form field by name.", Permission.REVERSIBLE_WRITE, "medium",
        t_browser_fill, "field:str, value:str", "confirmation or field-not-found error"),
    "browser_submit": _tool(
        "browser_submit", "Submit the current form (creates the record).", Permission.IRREVERSIBLE_WRITE, "high",
        t_browser_submit, "", "server response"),
    "browser_screenshot": _tool(
        "browser_screenshot", "Save the current page as an HTML evidence file.", Permission.READ, "low",
        t_browser_screenshot, "path:str", "path"),
    "app_get": _tool(
        "app_get", "Read any sandbox app endpoint (read-only).", Permission.READ, "low",
        t_app_get, "path:str", "response body"),
    # --- finance-domain actions --------------------------------------- #
    "vendor_lookup": _tool(
        "vendor_lookup", "Look up a vendor's master record (status, terms, bank, tax id).",
        Permission.READ, "low", t_vendor_lookup, "vendor:str", "vendor master record"),
    "po_lookup": _tool(
        "po_lookup", "Find open purchase orders for a vendor and the closest amount match.",
        Permission.READ, "low", t_po_lookup, "vendor:str, amount?:number", "matching purchase orders"),
    "duplicate_check": _tool(
        "duplicate_check", "Check whether an invoice (vendor+amount) already exists.",
        Permission.READ, "low", t_duplicate_check, "vendor:str, amount:number, due_date?:str", "duplicate verdict"),
    "policy_check": _tool(
        "policy_check", "Evaluate the AP approval policy tier for an amount (deterministic).",
        Permission.READ, "low", t_policy_check, "amount:number, category?:str", "policy tier + approval requirement"),
    "currency_convert": _tool(
        "currency_convert", "Convert an amount between sandbox currencies.", Permission.READ, "low",
        t_currency_convert, "amount:num, frm:str, to:str", "converted amount"),
    "financial_calculator": _tool(
        "financial_calculator", "Evaluate a safe arithmetic expression (the model cannot do math reliably).",
        Permission.READ, "low", t_financial_calculator, "expression:str", "numeric result"),
    "generate_report": _tool(
        "generate_report", "Write a markdown report to the run's evidence folder.", Permission.REVERSIBLE_WRITE, "medium",
        t_generate_report, "title:str, content:str", "report path"),
    "create_payment": _tool(
        "create_payment", "Prepare a vendor payment in the payables system (irreversible write).",
        Permission.IRREVERSIBLE_WRITE, "high",
        t_create_payment, "vendor:str, amount:num, due_date:str, currency?:str", "payment record"),
    "update_vendor_bank": _tool(
        "update_vendor_bank", "Change a vendor's bank account (irreversible, fraud-sensitive write).",
        Permission.IRREVERSIBLE_WRITE, "high",
        t_update_vendor_bank, "vendor:str, bank_account:str", "confirmation"),
}


# Models rarely use the exact input name. Map common variants onto the canonical
# argument so a near-miss does not crash the run.
_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "vendor_lookup": {"vendor": ("vendor", "vendor_name", "name", "company", "supplier")},
    "po_lookup": {"vendor": ("vendor", "vendor_name", "name", "company", "supplier"),
                  "amount": ("amount", "value", "total", "invoice_amount")},
    "duplicate_check": {"vendor": ("vendor", "vendor_name", "name", "company", "supplier"),
                        "amount": ("amount", "value", "total", "invoice_amount"),
                        "due_date": ("due_date", "due", "duedate", "date")},
    "policy_check": {"amount": ("amount", "value", "total", "invoice_amount"),
                     "category": ("category", "type", "expense_type")},
    "currency_convert": {"amount": ("amount", "value"),
                         "frm": ("frm", "from", "from_currency", "source", "base"),
                         "to": ("to", "to_currency", "target", "quote")},
    "financial_calculator": {"expression": ("expression", "expr", "formula", "calculation")},
    "generate_report": {"title": ("title", "name", "report_title", "report_name"),
                        "content": ("content", "body", "text", "summary", "report")},
    "create_payment": {"vendor": ("vendor", "vendor_name", "name", "company", "supplier", "payee"),
                       "amount": ("amount", "value", "total"),
                       "due_date": ("due_date", "due", "duedate", "date"),
                       "currency": ("currency", "ccy")},
    "update_vendor_bank": {"vendor": ("vendor", "vendor_name", "name", "company", "supplier"),
                           "bank_account": ("bank_account", "bank", "account", "account_number",
                                            "new_bank_account", "bank_details")},
}


def normalize_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    spec = _ALIASES.get(name)
    if not spec:
        return dict(args)
    out = dict(args)
    for canonical, aliases in spec.items():
        if canonical in out:
            continue
        for alias in aliases:
            if alias in out:
                out[canonical] = out.pop(alias)
                break
    return out


def get_tool(name: str) -> Tool | None:
    return TOOLS.get(name)


def run_tool(name: str, ctx: ToolContext, args: dict[str, Any]) -> str:
    tool = get_tool(name)
    if tool is None:
        raise ToolError(f"unknown tool '{name}'; choose from: {', '.join(sorted(TOOLS))}")
    return tool.run(ctx, **normalize_args(name, args))