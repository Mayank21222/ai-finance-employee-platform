"""Deterministic verifier. Only this code may mark a run complete.

It reads the sandbox app through a read-only path and compares what it
finds against the intended outcome. The model's word is never accepted.
"""

from __future__ import annotations

import httpx

from ai_operator.models import Verification
from ai_operator.tools import APP_BASE_URL


class Verifier:
    def __init__(self, base_url: str = APP_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=10)

    def read_records(self, vendor: str | None = None) -> list[dict]:
        r = self._client.get("/api/invoices", params={"vendor": vendor} if vendor else None)
        r.raise_for_status()
        return r.json()

    def verify_invoice(self, vendor: str, amount: float, due_date: str) -> Verification:
        expected = f"vendor {vendor}, amount {amount}, due date {due_date}"
        records = self.read_records(vendor)
        found = [
            r for r in records
            if float(r.get("amount", 0)) == float(amount) and r.get("due_date") == str(due_date)
        ]
        found_desc = ", ".join(
            f"record {r.get('id')}: {r.get('vendor')} amount {r.get('amount')} due {r.get('due_date')}" for r in found
        ) or f"{len(records)} record(s) for '{vendor}' but none matched"
        return Verification(
            checked=f"invoice record for {vendor}",
            expected=expected,
            found=found_desc[:300],
            matched=bool(found),
        )

    def read_payments(self, vendor: str | None = None) -> list[dict]:
        r = self._client.get("/api/payments", params={"vendor": vendor} if vendor else None)
        r.raise_for_status()
        return r.json()

    def verify_payment(self, vendor: str, amount: float, due_date: str) -> Verification:
        expected = f"payment for vendor {vendor}, amount {amount}, due date {due_date}"
        records = self.read_payments(vendor)
        found = [
            r for r in records
            if float(r.get("amount", 0)) == float(amount) and r.get("due_date") == str(due_date)
        ]
        found_desc = ", ".join(
            f"payment {r.get('id')}: {r.get('vendor')} amount {r.get('amount')} due {r.get('due_date')}" for r in found
        ) or f"{len(records)} payment(s) for '{vendor}' but none matched"
        return Verification(
            checked=f"payment record for {vendor}",
            expected=expected,
            found=found_desc[:300],
            matched=bool(found),
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass