import httpx

from ai_operator.verifier import Verifier


def test_verifier_mismatch_then_match(app_server, reset_db):
    v = Verifier(app_server)
    miss = v.verify_invoice("Acme Corp", 42500, "2026-07-30")
    assert miss.matched is False

    httpx.post(app_server + "/invoice/new",
               data={"vendor": "Acme Corp", "amount": "42500", "due_date": "2026-07-30"}, timeout=5)
    hit = v.verify_invoice("Acme Corp", 42500, "2026-07-30")
    assert hit.matched is True
    assert "42500" in hit.found


def test_verifier_rejects_wrong_amount(app_server, reset_db):
    httpx.post(app_server + "/invoice/new",
               data={"vendor": "Acme Corp", "amount": "4250", "due_date": "2026-07-30"}, timeout=5)
    v = Verifier(app_server)
    result = v.verify_invoice("Acme Corp", 42500, "2026-07-30")
    assert result.matched is False
