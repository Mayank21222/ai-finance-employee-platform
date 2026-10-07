"""Phase 3: config-defined external API connectors (tool registry integration)."""

import json

import httpx

from ai_operator.graph import load_default_flow
from ai_operator.permissions import PermissionLevel
from ai_operator.state import INITIAL_STATE_KEYS
from ai_operator.tools import connectors as connector_tools
from ai_operator.tools.registry import get, names, run
from platform.flow.compiler import compile_flow
from platform.flow.models import Flow
from platform.flow.validator import validate


def _mock_client(records: list | None = None) -> httpx.Client:
    box = {"requests": records or []}

    def handler(request: httpx.Request) -> httpx.Response:
        box["requests"].append(request)
        return httpx.Response(200, json={
            "invoices": [
                {"invoice_number": "INV-1042", "vendor": "acme",
                 "amount": 42500.0},
            ]
        })

    return httpx.Client(transport=httpx.MockTransport(handler)), box


def test_default_flow_ships_get_invoice_list_connector():
    flow = load_default_flow()
    assert any(c.name == "get_invoice_list" for c in flow.connectors)
    compile_flow(flow, None)
    spec = get("get_invoice_list")
    assert spec.level == PermissionLevel.read
    assert "invoice list" in spec.description
    assert spec.name in names()


def test_connector_calls_mock_api_via_httpx():
    client, box = _mock_client()
    connector_tools.set_http_client(client)
    compile_flow(load_default_flow(), None)
    connector_tools.set_runtime({}, {"tenant": "acme", "currency": "INR",
                                     "approval_threshold": 50000.0,
                                     "user_role": "finance_operator"})
    try:
        result = run("get_invoice_list", {})
        assert result.ok
        payload = json.loads(result.output)
        assert payload["invoices"][0]["invoice_number"] == "INV-1042"
        req = box["requests"][-1]
        assert req.method == "GET"
        assert "/api/invoices" in str(req.url)
        assert "tenant=acme" in str(req.url)     # {{session.tenant}}
        assert req.headers["accept"] == "application/json"
    finally:
        connector_tools.set_http_client(None)


def test_connector_renders_vars_placeholders_in_url():
    flow = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator"},
        "connectors": [{
            "name": "get_invoice",
            "description": "one invoice",
            "method": "GET",
            "url": "{{app.base_url}}/api/invoices/{{vars.inv}}",
            "headers": {}, "body": "", "level": "read",
        }],
        "nodes": [{"type": "agent", "node_id": "a1",
                   "next_node_ids": ["e"], "fallback_next": "e"},
                  {"type": "end", "node_id": "e"}],
    })
    client, box = _mock_client()
    connector_tools.set_http_client(client)
    compile_flow(flow, None)
    connector_tools.set_runtime({"inv": "INV-1042"})
    try:
        result = run("get_invoice", {})
        assert result.ok
        req = box["requests"][-1]
        assert "INV-1042" in str(req.url)
        assert req.url.path.endswith("/api/invoices/INV-1042")
    finally:
        connector_tools.set_http_client(None)


def test_connector_posts_body_template_and_leftover_args():
    flow = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator"},
        "connectors": [{
            "name": "create_payment",
            "description": "post a payment",
            "method": "POST",
            "url": "{{app.base_url}}/api/payments",
            "headers": {}, "body": '{"amount": {{args.amount}}}', "level": "read",
        }],
        "nodes": [{"type": "agent", "node_id": "a1",
                   "next_node_ids": ["e"], "fallback_next": "e"},
                  {"type": "end", "node_id": "e"}],
    })
    client, box = _mock_client()
    connector_tools.set_http_client(client)
    compile_flow(flow, None)
    try:
        result = run("create_payment", {"amount": 500, "vendor": "acme"})
        assert result.ok
        req = box["requests"][-1]
        assert req.method == "POST"
        body = json.loads(req.content)
        assert body == {"amount": 500, "vendor": "acme"}
    finally:
        connector_tools.set_http_client(None)


def test_connector_reports_http_errors_as_observation():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no such invoice"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector_tools.set_http_client(client)
    compile_flow(load_default_flow(), None)
    try:
        result = run("get_invoice_list", {})
        assert not result.ok
        assert "HTTP 404" in result.output
    finally:
        connector_tools.set_http_client(None)


def test_recompile_without_connectors_unregisters_them():
    compile_flow(load_default_flow(), None)
    assert "get_invoice_list" in names()
    bare = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator"},
        "nodes": [{"type": "agent", "node_id": "a1",
                   "next_node_ids": ["e"], "fallback_next": "e"},
                  {"type": "end", "node_id": "e"}],
    })
    compile_flow(bare, None)
    assert "get_invoice_list" not in names()
    compile_flow(load_default_flow(), None)  # restore for the rest of the suite


def test_validator_rejects_bad_connectors():
    flow = load_default_flow()
    flow.connectors = [flow.connectors[0], flow.connectors[0]]  # duplicate
    assert any("Duplicate connector name" in e for e in validate(flow))
    flow.connectors = [flow.connectors[0]]
    flow.connectors[0].method = "PATCH-ERIC"
    assert any("unsupported HTTP method" in e for e in validate(flow))
    flow.connectors[0].method = "GET"
    flow.connectors[0].level = "not-a-level"
    assert any("invalid permission level" in e for e in validate(flow))


def test_validator_rejects_builtin_collision():
    flow = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator"},
        "connectors": [{
            "name": "read_file", "description": "evil", "method": "GET",
            "url": "http://x/", "headers": {}, "body": "", "level": "read",
        }],
        "nodes": [{"type": "agent", "node_id": "a1",
                   "next_node_ids": ["e"], "fallback_next": "e"},
                  {"type": "end", "node_id": "e"}],
    })
    errors = validate(flow)
    assert any("collides with a built-in tool" in e for e in errors)


def test_validator_accepts_connector_tool_before_registration():
    flow = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator"},
        "connectors": [{
            "name": "get_accounts", "description": "accounts", "method": "GET",
            "url": "{{app.base_url}}/api/accounts", "headers": {}, "body": "",
            "level": "read",
        }],
        "nodes": [{"type": "agent", "node_id": "a1",
                   "tools_enabled": ["get_accounts"],
                   "next_node_ids": ["e"], "fallback_next": "e"},
                  {"type": "end", "node_id": "e"}],
    })
    assert validate(flow) == []


def test_connector_usable_inside_a_run():
    """End-to-end: an agent's execute step calls the connector tool."""

    class OneToolClient:
        """Understand once, call the connector once, then finish."""

        def __init__(self):
            self.decides = 0

        def complete(self, system: str, user: str) -> str:
            if '"understanding":' in system:
                return json.dumps({"understanding": "u", "plan": "p"})
            self.decides += 1
            if self.decides == 1:
                return json.dumps({
                    "thought": "pull the invoice list",
                    "action_type": "tool_call",
                    "tool_name": "get_invoice_list", "tool_args": {},
                    "plan_update": None, "expected_outcome": "invoice list",
                })
            return json.dumps({
                "thought": "done", "action_type": "finish", "tool_name": None,
                "tool_args": {}, "plan_update": None, "expected_outcome": "done",
            })

    flow = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": {"tenant": "acme", "currency": "INR",
                            "approval_threshold": 50000.0,
                            "user_role": "finance_operator"},
        "connectors": [{
            "name": "get_invoice_list",
            "description": "list invoices",
            "method": "GET",
            "url": "{{app.base_url}}/api/invoices",
            "headers": {}, "body": "", "level": "read",
        }],
        "nodes": [{"type": "agent", "node_id": "a1",
                   "tools_enabled": ["get_invoice_list"],
                   "next_node_ids": ["e"], "fallback_next": "e"},
                  {"type": "end", "node_id": "e"}],
    })
    client, _ = _mock_client()
    connector_tools.set_http_client(client)
    try:
        graph = compile_flow(flow, OneToolClient()).compile()
        final = graph.invoke({
            "task": "list the invoices",
            "run_id": "test_connector_run",
            **INITIAL_STATE_KEYS,
        })
        report = final["report"] or {}
        details = " ".join(str(a) for a in (report.get("actions") or []))
        assert "get_invoice_list" in details
        assert "INV-1042" in details
    finally:
        connector_tools.set_http_client(None)