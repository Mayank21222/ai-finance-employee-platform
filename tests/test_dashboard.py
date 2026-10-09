"""Dashboard tests: routes resolve and the shared shell is well-formed.

These guard the UI bugs: the sidebar /flow link and per-employee /run/{id}
buttons previously 404'd, the flow node panel was loaded over SSE against a
plain-HTML endpoint, and the shell left the .top/.toggle divs unclosed — which
nested the entire page inside the pill-shaped toggle (everything looked
elliptical) and echoed the active page name in the header.
"""

from __future__ import annotations

from html.parser import HTMLParser

from builder.dashboard import app
from fastapi.testclient import TestClient

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.balanced = True

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.balanced = False


def test_all_screens_and_links_resolve():
    client = TestClient(app)
    paths = ["/", "/agents", "/agents/accounts-payable-employee",
             "/flow", "/flow/accounts-payable-employee",
             "/run", "/run/accounts-payable-employee", "/history", "/logs"]
    for path in paths:
        r = client.get(path, follow_redirects=True)
        assert r.status_code == 200, f"{path} -> {r.status_code}"


def test_flow_node_panel_returns_html(tmp_path, monkeypatch):
    """Uses an isolated store: the real store's employees are user data and
    can be deleted at any time (the demo employee is gone as of this session)."""
    client, emp = _flow_client(tmp_path, monkeypatch)
    r = client.get(f"/flow/{emp}/node/n1")
    assert r.status_code == 200
    assert "slide" in r.text
    assert "Step configuration" in r.text


def test_node_panel_uses_fetch_not_sse(tmp_path, monkeypatch):
    from builder import dashboard

    client, emp = _flow_client(tmp_path, monkeypatch)
    r = client.get(f"/flow/{emp}")
    assert "loadPanel(" in r.text
    assert "loadPanel" in open(dashboard.__file__, encoding="utf-8").read()


def test_shell_html_is_balanced():
    """Regression: unclosed .top/.toggle divs nested the body inside the
    pill-shaped toggle, making all content render elliptical."""
    client = TestClient(app)
    for path in ["/agents", "/flow/accounts-payable-employee", "/run", "/history", "/logs"]:
        parser = _Balance()
        parser.feed(client.get(path).text)
        assert parser.balanced, f"unbalanced tags on {path}"
        assert not parser.stack, f"unclosed tags on {path}: {parser.stack[:5]}"


def test_body_is_not_nested_inside_header():
    client = TestClient(app)
    for path in ["/agents", "/flow/accounts-payable-employee", "/run", "/history"]:
        html = client.get(path).text
        assert html.index('<div class="wrap">') > html.index("</header>"), path


def test_header_does_not_echo_active_page_name():
    client = TestClient(app)
    routes = {"Agents": "/agents", "Flow": "/flow", "Run": "/run",
              "History": "/history", "Logs": "/logs"}
    for name, route in routes.items():
        html = client.get(route, follow_redirects=True).text
        header = html[html.index("<header"):html.index("</header>") + 9]
        assert f">{name}<" not in header, f"{name} duplicated in header"


def test_run_page_is_a_dynamic_chat():
    client = TestClient(app)
    r = client.get("/run")
    assert r.status_code == 200
    text = r.text
    for marker in ("chat-log", "chat-text", "chat-status", "chatSend(", "/api/run/start",
                   "EventSource", "addEventListener('approval'"):
        assert marker in text, f"missing chat marker: {marker}"


def test_translate_distinguishes_approval_and_answer_records():
    """The approval record and the tool result used to render as the same line
    ("Checked the payment policy" shown twice); answers rendered as "ask_human "."""
    from builder.dashboard import _translate

    _, text = _translate({"action_type": "tool_call", "tool": "policy_check",
                          "summary": "Approval requested: amount 120,000.00 INR requires "
                                     "finance_approval. Decision: yes"})
    assert text == "You approved: policy_check"
    _, text = _translate({"action_type": "tool_call", "tool": "create_payment",
                          "summary": "Approval requested: irreversible write. Decision: rejected"})
    assert text == "You rejected: create_payment"
    _, text = _translate({"action_type": "ask_human", "tool": None, "summary": "Human said: yes"})
    assert text == "You said: yes"
    # the plain tool result keeps its friendly mapping
    _, text = _translate({"action_type": "tool_call", "tool": "policy_check",
                          "summary": "policy_check -> tier: finance_approval"})
    assert text == "Checked the payment policy"
    # no bare "ask_human " fallback for clarification records
    _, text = _translate({"action_type": "ask_human", "tool": None, "summary": ""})
    assert text.strip() != "ask_human"


def test_chat_routes_answers_and_blocks_competing_runs():
    """Typing in the main box during a live run must answer the pending
    interruption instead of starting a second, competing run."""
    client = TestClient(app)
    html = client.get("/run").text
    for marker in ("RUN.awaiting==='approval'", "RUN.awaiting==='question'",
                   "one task at a time", "answerApproval(", "answerReply(",
                   "RUN.done"):
        assert marker in html, f"missing chat routing marker: {marker}"


def test_run_reply_unknown_run_404():
    client = TestClient(app)
    assert client.post("/run/reply/does-not-exist", params={"answer": "hi"}).status_code == 404


def test_api_logs_returns_events_and_logs_screen_renders_them(tmp_path, monkeypatch):
    import ai_operator.observability as obs

    monkeypatch.setattr(obs, "_configured", True)
    monkeypatch.setattr(obs, "_event_path", str(tmp_path / "traces.jsonl"))
    obs.set_run("run-xyz")
    obs.log_event("tool_call", tool="vendor_lookup", ok=True, result="found")
    obs.log_event("final_report", status="completed", summary="done")

    client = TestClient(app)
    data = client.get("/api/logs").json()
    names = [e["event"] for e in data]
    assert "tool_call" in names and "final_report" in names
    assert any(e.get("run_id") == "run-xyz" for e in data)

    filtered = client.get("/api/logs", params={"run_id": "nope"}).json()
    assert filtered == []

    page = client.get("/logs")
    assert page.status_code == 200
    assert "traces.jsonl" in page.text


def _flow_client(tmp_path, monkeypatch):
    """A dashboard wired to a fresh store with one seeded employee."""
    from builder import dashboard
    from builder.flow import new_employee
    from builder.store import Store

    st = Store(str(tmp_path / "b.db"))
    st.save_employee(new_employee("Flow Tester", "Accounts Payable"))
    monkeypatch.setattr(dashboard, "store", st)
    return TestClient(app), "flow-tester"


def test_flow_screen_is_an_interactive_editor(tmp_path, monkeypatch):
    """The flow screen renders a canvas the JS drives: drag, palette, per-node
    next-step selector, auto-arrange — not a static list of boxes."""
    client, emp = _flow_client(tmp_path, monkeypatch)
    html = client.get(f"/flow/{emp}").text
    for marker in ('id="canvas"', "window.FLOW=", "window.LIB=", "togglePalette(",
                   "autoArrange(", "next-sel", "pointerdown", "savePositions",
                   "deleteNode(", "window.saveNode="):
        assert marker in html, f"missing flow editor marker: {marker}"
    # the node library must be available to the add-step palette
    assert "finance" in html and "Invoice" in html


def test_node_panel_has_real_controls_not_a_fake_save(tmp_path, monkeypatch):
    client, emp = _flow_client(tmp_path, monkeypatch)
    r = client.get(f"/flow/{emp}/node/n1")
    assert r.status_code == 200
    for marker in ("pf-name", "pf-type", "pf-note", "pf-next", "Connects to",
                   "saveNode('", "deleteNode('"):
        assert marker in r.text, f"missing panel control: {marker}"
    assert "Saved (prototype)" not in r.text, "panel still fakes the save"
    # the panel lists the other steps as connect targets, excluding itself
    assert '<option value="n1"' not in r.text.split('id="pf-next"')[1]


def test_flow_node_lifecycle_add_connect_move_delete(tmp_path, monkeypatch):
    client, emp = _flow_client(tmp_path, monkeypatch)

    # create: appended after n9 (which has no next yet), so it auto-connects
    r = client.post(f"/api/flow/{emp}/nodes", data={"type": "logic", "name": "Branch", "after": "n9"})
    assert r.status_code == 200
    j = r.json()
    nid = j["node"]["id"]
    assert nid == "n10"
    assert any(e["source"] == "n9" and e["target"] == nid for e in j["flow"]["edges"])

    # navigate: point n1 at the new step and edit its fields
    r = client.post(f"/api/flow/{emp}/nodes/n1",
                    data={"name": "Vendor Lookup!", "type": "logic",
                          "instructions": "check the vendor", "next": nid})
    assert r.status_code == 200
    flow = r.json()["flow"]
    n1 = next(n for n in flow["nodes"] if n["id"] == "n1")
    assert n1["name"] == "Vendor Lookup!" and n1["type"] == "logic"
    assert n1["note"] == "check the vendor"
    assert any(e["source"] == "n1" and e["target"] == nid for e in flow["edges"])
    assert not any(e["source"] == "n1" and e["target"] == "n2" for e in flow["edges"])

    # drag: positions persist
    r = client.post(f"/api/flow/{emp}/positions",
                    json={"positions": {nid: {"x": 500, "y": 60}}})
    assert r.status_code == 200 and r.json()["ok"]
    flow = client.get(f"/api/flow/{emp}").json()["flow"]
    moved = next(n for n in flow["nodes"] if n["id"] == nid)
    assert (moved["x"], moved["y"]) == (500, 60)

    # delete: step and every edge touching it go away; start is protected
    assert client.post(f"/api/flow/{emp}/nodes/start/delete").status_code == 400
    r = client.post(f"/api/flow/{emp}/nodes/{nid}/delete")
    assert r.status_code == 200
    flow = r.json()["flow"]
    assert not any(n["id"] == nid for n in flow["nodes"])
    assert not any(e["source"] == nid or e["target"] == nid for e in flow["edges"])
    assert client.post(f"/api/flow/{emp}/nodes/{nid}/delete").status_code == 404


def test_flow_api_errors_for_unknown_employee(tmp_path, monkeypatch):
    client, _ = _flow_client(tmp_path, monkeypatch)
    assert client.get("/api/flow/nope").status_code == 404
    assert client.post("/api/flow/nope/nodes", data={"name": "x"}).status_code == 404
    assert client.post("/api/flow/nope/nodes/n1/delete").status_code == 404


def test_agent_create_duplicate_delete_lifecycle(tmp_path, monkeypatch):
    client, _ = _flow_client(tmp_path, monkeypatch)

    # create returns JSON for the async form, redirect for a plain form post
    r = client.post("/agents", data={"name": "New Buddy", "role": "CFO Assistant"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200
    emp_id = r.json()["id"]
    assert emp_id == "new-buddy"
    r = client.post("/agents", data={"name": "New Buddy 2", "role": "CFO Assistant"},
                    headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303

    # duplicate gets its own id and copies the setup
    r = client.post(f"/agents/{emp_id}/duplicate")
    assert r.status_code == 200
    copy_id = r.json()["id"]
    assert copy_id != emp_id
    copy = client.get(f"/agents/{copy_id}")
    assert copy.status_code == 200 and "(copy)" in copy.text

    # delete removes it; a second delete (and unknown ids) 404
    assert client.post(f"/agents/{copy_id}/delete").json().get("ok") is True
    assert client.post(f"/agents/{copy_id}/delete").status_code == 404
    assert client.post("/agents/never-existed/delete").status_code == 404
    assert client.get(f"/agents/{copy_id}", follow_redirects=False).status_code == 307  # redirects home


def test_run_screen_offers_creation_when_no_employees(tmp_path, monkeypatch):
    from builder import dashboard
    from builder.store import Store

    monkeypatch.setattr(dashboard, "store", Store(str(tmp_path / "empty.db")))
    monkeypatch.setattr(dashboard, "_seed", lambda: None)  # user wiped them all
    client = TestClient(app)
    r = client.get("/run")
    assert r.status_code == 200
    assert "Create an employee" in r.text


def test_history_detail_renders_report_object(tmp_path, monkeypatch):
    """Regression: a run with a FinalReport 500'd on /history/{id} because
    FinalReport was not JSON-serializable."""
    from ai_operator.models import FinalReport, RunRecord, Verification
    from builder import dashboard
    from builder.store import Store

    st = Store(str(tmp_path / "b.db"))
    st.save_run(RunRecord(
        id="run-1", employee_id="e1", employee_name="AP Employee",
        task="Pay Acme", status="completed", duration=1.5,
        report=FinalReport(status="completed", summary="Paid Acme invoice",
                           actions=["vendor_lookup", "create_payment"],
                           verification=Verification(checked="payment", expected="created",
                                                     found="created", matched=True),
                           evidence=["inv-1"])))
    monkeypatch.setattr(dashboard, "store", st)
    client = TestClient(app)
    r = client.get("/history/run-1")
    assert r.status_code == 200
    assert "Paid Acme invoice" in r.text