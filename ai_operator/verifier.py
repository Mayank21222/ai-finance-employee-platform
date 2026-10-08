"""Deterministic verification: read-only check of real app state vs source docs.

The model has no say here. The verifier derives ground truth from the task text
and the invoice files on disk, then compares against the payables app's
read-only API. Task kinds it cannot check are reported honestly as unverified.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from ai_operator.tools.files import WORKSPACE

INVOICE_DIR = WORKSPACE / "company_data" / "invoices"
DEFAULT_APP_URL = "http://127.0.0.1:8000"


class ExpectedRecord(BaseModel):
    vendor: str
    amount: float
    due_date: str
    source_invoice: str


class VerificationResult(BaseModel):
    checked: str
    expected: dict[str, Any]
    found: dict[str, Any] | list[Any]
    matched: bool
    details: str
    model_check: dict[str, Any] | None = None
    """Phase 4: data-model field type/format checks over written variables."""


def check_model_fields(
    variables: dict[str, Any],
    fields: list[Any],
    model_name: str | None = None,
) -> dict[str, Any] | None:
    """Type/format-check the data model fields the run actually wrote.

    Returns None when no model field was written (nothing to check). Each
    written field is validated against its declared type: number fields must
    parse as numbers, date fields as ISO dates, boolean fields as booleans,
    text fields as non-empty scalars. The verifier owns these checks - they
    never live in the agent prompt.
    """
    present = {f.name: variables.get(f.name) for f in fields
               if f.name in variables and variables.get(f.name) is not None}
    if not present:
        return None
    per: dict[str, dict[str, Any]] = {}
    matched = True
    for field in fields:
        if field.name not in present:
            continue
        ok, note = _check_one(field, present[field.name])
        per[field.name] = {"value": present[field.name], "type": field.type,
                           "required": field.required, "ok": ok, "note": note}
        matched = matched and ok
    return {
        "model": model_name or "",
        "matched": matched,
        "fields": per,
        "details": (
            "all written data-model fields passed type checks"
            if matched else
            "data-model field type checks failed: "
            + "; ".join(f"{k}: {v['note']}" for k, v in per.items()
                        if not v["ok"])
        ),
    }


def _check_one(field: Any, value: Any) -> tuple[bool, str]:
    name = field.name
    if isinstance(value, bool):
        scalar = value
    elif value is None or value == "":
        return (False, "empty value") if field.required else (True, "optional field empty")
    else:
        scalar = value
    if field.type in ("text",):
        return (True, "text field readable")
    if field.type == "number":
        if isinstance(scalar, bool):
            return (False, "expected a number, got a boolean")
        try:
            cleaned = str(scalar).replace(",", "").replace("₹", "").strip()
            float(cleaned)
            return (True, "parses as a number")
        except (TypeError, ValueError):
            return (False, f"expected a number, got {scalar!r}")
    if field.type == "date":
        import datetime as _dt

        if hasattr(scalar, "isoformat"):
            scalar = scalar.isoformat()
        try:
            _dt.date.fromisoformat(str(scalar).strip())
            return (True, "valid ISO date")
        except (TypeError, ValueError):
            return (False, f"expected an ISO date (YYYY-MM-DD), got {scalar!r}")
    if field.type == "boolean":
        if isinstance(scalar, bool):
            return (True, "boolean field")
        if str(scalar).strip().lower() in ("true", "yes", "1", "on", "false",
                                           "no", "0", "off"):
            return (True, "boolean-like value")
        return (False, f"expected a boolean, got {scalar!r}")
    return (True, f"{field.type} not type-checked")


def vendor_from_task(task: str) -> str | None:
    """Deterministically pull the vendor name from the request text."""
    m = re.search(r"\bfrom\s+([^:,;.]+)", task)
    if not m:
        return None
    vendor = m.group(1).strip().strip('"')
    return vendor or None


def latest_invoice_for(vendor: str, invoice_dir: Path = INVOICE_DIR) -> ExpectedRecord | None:
    """Parse every invoice file for the vendor; return the one with the latest Invoice Date."""
    best: tuple[str, ExpectedRecord] | None = None
    for path in sorted(invoice_dir.glob("*.txt")):
        text = path.read_text(errors="replace")
        if not re.search(rf"^Vendor:\s*{re.escape(vendor)}\s*$", text, flags=re.M):
            continue
        date_m = re.search(r"^Invoice Date:\s*(\S+)", text, flags=re.M)
        amount_m = re.search(r"^Amount:\s*(?:INR|₹)\s*([\d,]+(?:\.\d+)?)", text, flags=re.M)
        due_m = re.search(r"^Due Date:\s*(\S+)", text, flags=re.M)
        if not (date_m and amount_m and due_m):
            continue
        record = ExpectedRecord(
            vendor=vendor,
            amount=float(amount_m.group(1).replace(",", "")),
            due_date=due_m.group(1),
            source_invoice=str(path.relative_to(WORKSPACE)),
        )
        if best is None or date_m.group(1) > best[0]:
            best = (date_m.group(1), record)
    return best[1] if best else None


def fetch_records(vendor: str, app_url: str = DEFAULT_APP_URL,
                  transport: httpx.BaseTransport | None = None) -> list[dict[str, Any]]:
    params = {"vendor": vendor}
    if transport is not None:
        client = httpx.Client(transport=transport, base_url=app_url)
    else:
        client = httpx.Client(base_url=app_url, timeout=10.0)
    with client:
        resp = client.get("/api/invoices", params=params)
        resp.raise_for_status()
        return list(resp.json())


def verify_invoice_entry(task: str, app_url: str = DEFAULT_APP_URL,
                         transport: httpx.BaseTransport | None = None) -> VerificationResult:
    vendor = vendor_from_task(task)
    if vendor is None:
        return VerificationResult(
            checked="invoice entry (vendor not found in task text)",
            expected={}, found=[], matched=False,
            details="could not determine the vendor from the request",
        )
    expected = latest_invoice_for(vendor)
    if expected is None:
        return VerificationResult(
            checked=f"invoice entry for {vendor}",
            expected={"vendor": vendor}, found=[], matched=False,
            details=f"no parseable invoice files found for {vendor}",
        )
    try:
        found = fetch_records(vendor, app_url, transport)
    except httpx.HTTPError as exc:
        return VerificationResult(
            checked=f"invoice entry for {vendor}",
            expected=expected.model_dump(), found=[], matched=False,
            details=f"could not reach the payables read API: {exc}",
        )
    match = next(
        (
            r for r in found
            if abs(float(r.get("amount", -1)) - expected.amount) < 0.01
            and str(r.get("due_date")) == expected.due_date
        ),
        None,
    )
    return VerificationResult(
        checked=f"payables record for {vendor} vs {expected.source_invoice}",
        expected=expected.model_dump(),
        found=found,
        matched=match is not None,
        details=(
            f"found matching record id={match['id']}"
            if match
            else f"no record matches amount={expected.amount} due_date={expected.due_date}; "
                 f"records in system: {found}"
        ),
    )


def verify_task(task: str, app_url: str = DEFAULT_APP_URL,
                transport: httpx.BaseTransport | None = None) -> VerificationResult:
    """Entry point: dispatch on task kind, honestly reporting unsupported tasks."""
    if "invoice" in task.lower():
        return verify_invoice_entry(task, app_url, transport)
    return VerificationResult(
        checked="task-type support",
        expected={}, found=[], matched=False,
        details="the verifier has no deterministic checker for this task kind yet; "
                "a human must confirm the outcome",
    )
