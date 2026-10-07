"""Deterministic verifier: ground truth from disk vs the app's read-only API."""

from __future__ import annotations

import httpx

from ai_operator.verifier import (
    latest_invoice_for,
    vendor_from_task,
    verify_invoice_entry,
    verify_task,
)

TASK = (
    "Process the latest invoice from Acme Corp: enter the amount and due date "
    "into the payables system and tell me when it's done."
)


def test_vendor_parsed_from_task():
    assert vendor_from_task(TASK) == "Acme Corp"


def test_vendor_missing_returns_none():
    assert vendor_from_task("Process the quarterly report today.") is None


def test_latest_invoice_is_newest_by_date():
    record = latest_invoice_for("Acme Corp")
    assert record is not None
    assert record.amount == 42500.0
    assert record.due_date == "2026-07-30"
    assert record.source_invoice.endswith("acme_2026-07-20.txt")


def test_unknown_vendor_has_no_invoice():
    assert latest_invoice_for("No Such Vendor") is None


def test_empty_system_reports_mismatch(payables_db, asgi_transport):
    result = verify_invoice_entry(TASK, transport=asgi_transport)
    assert result.matched is False
    assert "no record matches" in result.details


def test_matching_record_verifies(payables_db, asgi_transport):
    payables_db.add_invoice("Acme Corp", 42500.0, "2026-07-30")
    result = verify_invoice_entry(TASK, transport=asgi_transport)
    assert result.matched is True
    assert "id=" in result.details


def test_wrong_amount_is_ignored(payables_db, asgi_transport):
    payables_db.add_invoice("Acme Corp", 99999.0, "2026-07-30")
    result = verify_invoice_entry(TASK, transport=asgi_transport)
    assert result.matched is False


def test_wrong_due_date_is_ignored(payables_db, asgi_transport):
    payables_db.add_invoice("Acme Corp", 42500.0, "2026-12-31")
    result = verify_invoice_entry(TASK, transport=asgi_transport)
    assert result.matched is False


def test_unsupported_task_kind_is_honest():
    result = verify_task("Send a summary email to the board about Q3 results.")
    assert result.matched is False
    assert "no deterministic checker" in result.details


def test_unreachable_app_reports_connection_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    result = verify_invoice_entry(TASK, transport=transport)
    assert result.matched is False
    assert "could not reach" in result.details
