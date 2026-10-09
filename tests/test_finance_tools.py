"""Finance-domain tools: the deterministic actions an LLM cannot do itself."""

from __future__ import annotations


def test_search_files_tokenizes_multi_word_queries(tmp_path):
    """Regression: 'Acme invoice' used to be matched as one literal substring
    and found nothing (Acme_INV-003.txt), dead-ending the run."""
    from ai_operator.tools import ToolContext, t_search_files

    (tmp_path / "Acme_INV-003.txt").write_text("Invoice for Acme Corp, amount 42500")
    (tmp_path / "Globex_INV-101.txt").write_text("Invoice for Globex, amount 900")
    ctx = ToolContext(base_url="http://x", data_dir=str(tmp_path), storage_dir=str(tmp_path))

    out = t_search_files(ctx, "Acme invoice")
    assert "Acme_INV-003.txt" in out
    assert "Globex" not in out
    # every term must match (AND, not OR)
    assert "No files" in t_search_files(ctx, "Acme nonexistent-term")
    # hint tells the model what to do next instead of dead-ending
    assert "list_knowledge" in t_search_files(ctx, "nothing-matches-this")

import os

import httpx
import pytest

from ai_operator.tools import ToolContext, ToolError, run_tool
from ai_operator.verifier import Verifier


@pytest.fixture()
def ctx(app_server, tmp_path):
    return ToolContext(base_url=app_server, storage_dir=str(tmp_path))


def test_vendor_lookup_found_and_missing(ctx):
    assert "Acme Corp" in run_tool("vendor_lookup", ctx, {"vendor": "Acme"})
    miss = run_tool("vendor_lookup", ctx, {"vendor": "Nonexistent"})
    assert "No vendor matching" in miss
    assert "Acme Corp" in miss  # lists known vendors


def test_tool_arg_aliases_are_normalized(ctx):
    # real models often send vendor_name/name instead of vendor
    assert "Acme Corp" in run_tool("vendor_lookup", ctx, {"vendor_name": "Acme"})
    assert "1800" in run_tool("financial_calculator", ctx, {"expr": "10000*0.18"})
    assert "USD" in run_tool("currency_convert", ctx, {"amount": 1000, "from": "INR", "to": "USD"})


def test_po_lookup_matches_closest(ctx):
    out = run_tool("po_lookup", ctx, {"vendor": "Acme Corp", "amount": 43000})
    assert "PO-1001" in out
    assert "Closest match" in out


def test_duplicate_check_detects_existing_invoice(ctx, app_server, reset_db):
    assert "No duplicate" in run_tool("duplicate_check", ctx, {"vendor": "Acme Corp", "amount": 5000})
    httpx.post(app_server + "/invoice/new",
               data={"invoice_number": "INV-DUP", "vendor": "Acme Corp",
                     "amount": "5000", "due_date": "2026-07-30"}, timeout=5)
    out = run_tool("duplicate_check", ctx, {"vendor": "Acme Corp", "amount": 5000, "due_date": "2026-07-30"})
    assert "DUPLICATE suspected" in out


def test_policy_check_tiers(ctx):
    assert "auto" in run_tool("policy_check", ctx, {"amount": 5000})
    assert "manager_approval" in run_tool("policy_check", ctx, {"amount": 20000})
    assert "finance_approval" in run_tool("policy_check", ctx, {"amount": 60000})


def test_currency_convert(ctx):
    out = run_tool("currency_convert", ctx, {"amount": 1000, "frm": "INR", "to": "USD"})
    assert "USD" in out and "12.00" in out
    with pytest.raises(ToolError):
        run_tool("currency_convert", ctx, {"amount": 1, "frm": "INR", "to": "XYZ"})


def test_financial_calculator_and_safety(ctx):
    assert "1800" in run_tool("financial_calculator", ctx, {"expression": "10000*0.18"})
    with pytest.raises(ToolError):
        run_tool("financial_calculator", ctx, {"expression": "__import__('os').system('x')"})


def test_create_payment_is_verified(ctx, app_server, reset_db):
    out = run_tool("create_payment", ctx,
                   {"vendor": "Acme Corp", "amount": 42500, "due_date": "2026-07-30"})
    assert "Payment prepared" in out
    v = Verifier(app_server).verify_payment("Acme Corp", 42500, "2026-07-30")
    assert v.matched is True
    assert v.checked.startswith("payment")


def test_update_vendor_bank_changes_master(ctx, reset_db):
    run_tool("update_vendor_bank", ctx, {"vendor": "Acme Corp", "bank_account": "HDFC ****9999"})
    assert "HDFC ****9999" in run_tool("vendor_lookup", ctx, {"vendor": "Acme Corp"})


def test_generate_report_writes_file(ctx, tmp_path):
    out = run_tool("generate_report", ctx, {"title": "AP Summary", "content": "all clear"})
    path = os.path.join(str(tmp_path), "reports", "ap-summary.md")
    assert os.path.isfile(path)
    assert "all clear" in open(path, encoding="utf-8").read()
    assert path in out
