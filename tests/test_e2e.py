import httpx
from langgraph.checkpoint.memory import InMemorySaver

from ai_operator.llm import StubModel
from ai_operator.runtime import Operator
from tests.conftest import drive

TASK = "Process the latest invoice from Acme Corp: enter the amount and due date into the payables system and tell me when it's done."


def _script(amount="42500", due="2026-07-30", vendor="Acme Corp", rename=False, verify_amount=None):
    url = "/invoice/new?fail=rename" if rename else "/invoice/new"
    steps = [
        {"thought": "find", "action_type": "tool_call", "tool_name": "search_files", "tool_args": {"query": "Acme"}},
        {"thought": "read latest", "action_type": "tool_call", "tool_name": "read_file", "tool_args": {}},
        {"thought": "policy", "action_type": "tool_call", "tool_name": "read_file", "tool_args": {"path": "company_data/policies.md"}},
        {"thought": "open", "action_type": "tool_call", "tool_name": "browser_navigate", "tool_args": {"url": url}},
        {"thought": "vendor", "action_type": "tool_call", "tool_name": "browser_fill", "tool_args": {"field": "vendor", "value": vendor}},
        {"thought": "amount", "action_type": "tool_call", "tool_name": "browser_fill", "tool_args": {"field": "amount", "value": amount}},
    ]
    if rename:
        steps += [
            {"thought": "try stale selector", "action_type": "tool_call", "tool_name": "browser_fill",
             "tool_args": {"field": "due_date", "value": due}},
            {"thought": "inspect the page", "action_type": "tool_call", "tool_name": "browser_read", "tool_args": {}},
            {"thought": "use real field", "action_type": "tool_call", "tool_name": "browser_fill",
             "tool_args": {"field": "due-date-field", "value": due}},
        ]
    else:
        steps.append({"thought": "due", "action_type": "tool_call", "tool_name": "browser_fill",
                      "tool_args": {"field": "due_date", "value": due}})
    steps += [
        {"thought": "submit", "action_type": "tool_call", "tool_name": "browser_submit", "tool_args": {}},
        {"thought": "verify", "action_type": "verify",
         "tool_args": {"vendor": vendor, "amount": verify_amount or amount, "due_date": due}},
        {"thought": "finish", "action_type": "finish"},
    ]
    return steps


def _operator(app_server, tmp_path, script):
    return Operator(model=StubModel(script), checkpointer=InMemorySaver(),
                    base_url=app_server, storage_dir=str(tmp_path))


def test_happy_path(app_server, reset_db, tmp_path):
    result = drive(_operator(app_server, tmp_path, _script()), TASK)
    report = result["report"]
    assert report["status"] == "verified_complete"
    assert report["verification"]["matched"] is True
    assert len(report["approvals"]) >= 1  # irreversible submit always gated
    record = httpx.get(app_server + "/api/invoices", params={"vendor": "Acme Corp"}, timeout=5).json()
    assert record and record[0]["amount"] == 42500.0


def test_recovery_from_renamed_field(app_server, reset_db, tmp_path):
    script = _script(amount="5000", verify_amount="5000", rename=True)
    result = drive(_operator(app_server, tmp_path, script), TASK)
    assert result["report"]["status"] == "verified_complete"
    # the stale-selector attempt must be visible in the trace, followed by recovery
    summaries = [r["summary"] for r in result["records"]]
    assert any("not found" in s for s in summaries), "expected the renamed-field error in the trace"
    assert any("Filled due-date-field" in s for s in summaries)


def test_verification_mismatch_fails_the_run(app_server, reset_db, tmp_path):
    # agent fills 4250 but claims/expects 42500 -> deterministic verifier must fail it
    script = _script(amount="4250", verify_amount="42500")
    result = drive(_operator(app_server, tmp_path, script), TASK)
    assert result["report"]["status"] == "failed"
    assert result["report"]["verification"]["matched"] is False


def test_read_only_task_finishes_completed(app_server, reset_db, tmp_path):
    script = [
        {"thought": "read", "action_type": "tool_call", "tool_name": "search_files", "tool_args": {"query": "Acme"}},
        {"thought": "done", "action_type": "finish"},
    ]
    result = drive(_operator(app_server, tmp_path, script), "What Acme files do we have?")
    assert result["report"]["status"] == "completed"


def test_read_only_verify_does_not_fail_the_run(app_server, reset_db, tmp_path):
    # a lookup task where the model still calls verify must NOT end "failed":
    # no write happened, so there is nothing for the verifier to match.
    script = [
        {"thought": "lookup", "action_type": "tool_call", "tool_name": "vendor_lookup",
         "tool_args": {"vendor_name": "Acme Corp"}},
        {"thought": "verify anyway", "action_type": "verify",
         "tool_args": {"vendor": "Acme Corp", "amount": "0", "due_date": ""}},
        {"thought": "done", "action_type": "finish"},
    ]
    result = drive(_operator(app_server, tmp_path, script), "What is Acme's bank account?")
    assert result["report"]["status"] == "completed"
    assert result["report"]["verification"]["matched"] is False


def test_alternating_lookup_loop_stops_early(app_server, reset_db, tmp_path):
    # a model can alternate two lookups (vendor, po, vendor, po, …) yet get the
    # same answer each time; the no-new-information guard must stop it early.
    v = {"action_type": "tool_call", "tool_name": "vendor_lookup", "tool_args": {"vendor_name": "Acme Corp"}}
    p = {"action_type": "tool_call", "tool_name": "po_lookup", "tool_args": {"vendor_name": "Acme Corp"}}
    script = [dict(v, thought=f"vendor {i}") if i % 2 == 0 else dict(p, thought=f"po {i}") for i in range(6)]
    result = drive(_operator(app_server, tmp_path, script), "Tell me about Acme.")
    assert len(result["records"]) <= 3, f"expected early stop, ran {len(result['records'])}"
    assert result["report"]["status"] == "completed"


def test_loop_guard_stops_repeated_action(app_server, reset_db, tmp_path):
    same = {"thought": "again", "action_type": "tool_call", "tool_name": "search_files",
            "tool_args": {"query": "Acme"}}
    script = [dict(same, thought=f"try {i}") for i in range(6)]
    result = drive(_operator(app_server, tmp_path, script), "Keep searching")
    # must not run all 6 repeats; the deterministic guard forces a stop
    assert len(result["records"]) <= 4
    assert result["report"]["status"] in ("completed", "failed")


def test_rejected_approval_does_not_write(app_server, reset_db, tmp_path):
    # deny the irreversible submit -> no record should exist
    script = _script(amount="5000", verify_amount="5000")
    result = drive(_operator(app_server, tmp_path, script), TASK, answers=["no"])
    record = httpx.get(app_server + "/api/invoices", params={"vendor": "Acme Corp"}, timeout=5).json()
    assert record == []
    assert result["report"]["status"] != "verified_complete"
