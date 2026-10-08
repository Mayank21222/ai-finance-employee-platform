"""External API connectors: tools defined in config, not in Python (Phase 3).

A connector is a registry ToolSpec built from the flow's `connectors` block:
a name, description, HTTP method, a URL template with {{vars.x}} / {{args.x}}
/ {{session.<key>}} / {{app.base_url}} placeholders, headers, a JSON body
template, and a permission level. The execute node populates the runtime
variable snapshot before each tool call; the handler renders the templates
and dispatches with httpx (overridable in tests). This lets a company reach
its own ERP/accounting API without writing code.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ConfigDict

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.registry import ToolSpec, register, unregister

TPL_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s*\}\}")

_variables: dict[str, Any] = {}
_session: dict[str, Any] = {}
_injected_client: httpx.Client | None = None
_default_client: httpx.Client | None = None
_connector_names: set[str] = set()

MAX_OUTPUT_LENGTH = 4000


class ConnectorArgs(BaseModel):
    """Connector tools accept any argument keys; validation is the URL/body."""

    model_config = ConfigDict(extra="allow")


def set_runtime(variables: dict[str, Any] | None,
                session: dict[str, Any] | None = None) -> None:
    """The per-step variable snapshot used to render {{vars.x}} placeholders."""
    global _variables, _session
    _variables = dict(variables or {})
    if session is not None:
        _session = dict(session)


def set_http_client(client: httpx.Client | None) -> None:
    """Test hook: inject an httpx.MockTransport-backed client."""
    global _injected_client
    _injected_client = client


def get_runtime() -> tuple[dict[str, Any], dict[str, Any]]:
    """The current step's (variables, session) snapshot.

    Phase 6 section 9: data-source query tools render their {{vars.*}} /
    {{session.*}} parameters from the same snapshot the execute node sets
    here, so both tool families see identical values without a second
    runtime path.
    """
    return dict(_variables), dict(_session)


def _client_for() -> httpx.Client:
    global _default_client
    if _injected_client is not None:
        return _injected_client
    if _default_client is None:
        _default_client = httpx.Client(timeout=10.0)
    return _default_client


def _render(template: str, args: dict[str, Any]) -> str:
    def repl(m: "re.Match[str]") -> str:
        kind, key = m.group(1), m.group(2)
        if kind == "vars":
            return str(_variables.get(key, ""))
        if kind == "args":
            return str(args.get(key, ""))
        if kind == "session":
            return str(_session.get(key, ""))
        if kind == "app" and key == "base_url":
            return os.environ.get("APP_URL", "http://127.0.0.1:8000")
        return m.group(0)

    return TPL_RE.sub(repl, template)


def _args_placeholders(connector: "Connector") -> set[str]:
    """All {{args.x}} keys the connector templates reference (consumed keys)."""
    keys: set[str] = set()
    for template in (connector.url, connector.body):
        for kind, key in TPL_RE.findall(template or ""):
            if kind == "args":
                keys.add(key)
    return keys


@dataclass(frozen=True)
class Connector:
    """Config-declared connector, ready to become a registry ToolSpec."""

    name: str
    description: str
    method: str
    url: str
    headers: dict[str, str]
    body: str
    level: PermissionLevel
    timeout: float


def _handler(connector: Connector) -> Callable[[BaseModel], str]:
    def handle(args: "ConnectorArgs") -> str:
        call_args = dict(args.model_dump(exclude_unset=True))
        consumed = _args_placeholders(connector)
        leftover = {k: v for k, v in call_args.items() if k not in consumed}
        url = _render(connector.url, call_args)
        headers = {k: _render(v, call_args)
                   for k, v in connector.headers.items()}
        method = connector.method.upper()
        params: dict[str, Any] | None = None
        content: str | None = None
        if method in ("GET", "HEAD", "DELETE"):
            params = leftover or None
        else:
            if connector.body:
                rendered = _render(connector.body, call_args)
                try:
                    body_dict = json.loads(rendered)
                except json.JSONDecodeError:
                    body_dict = {"raw": rendered}
            else:
                body_dict = {}
            body_dict.update(leftover)
            if body_dict:
                content = json.dumps(body_dict, default=str)
            headers.setdefault("Content-Type", "application/json")
        client = _client_for()
        resp = client.request(method, url, headers=headers or None,
                              params=params, content=content)
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                out = json.dumps(resp.json(), indent=2, default=str)
            except (ValueError, TypeError):
                out = resp.text or ""
        else:
            out = resp.text or ""
        if resp.is_error:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code} from {url}: {out[:1000]}",
                request=resp.request, response=resp,
            )
        return out[:MAX_OUTPUT_LENGTH] or "(empty response)"

    return handle


def registered_names() -> set[str]:
    """Connector names currently registered in the tool registry (Phase 3)."""
    return set(_connector_names)


def sync(connectors: list["Connector"]) -> list[str]:
    """Register exactly the configured connectors; return error strings.

    Called from compile_flow: the registry always holds the connectors of the
    flow currently being compiled, so recompiles and synthetic test flows do
    not leave stale connector tools behind.
    """
    errors: list[str] = []
    for name in list(_connector_names):
        unregister(name)
    _connector_names.clear()
    for connector in connectors:
        try:
            register(ToolSpec(
                name=connector.name,
                description=connector.description or connector.url,
                level=connector.level,
                args_model=ConnectorArgs,
                handler=_handler(connector),
                timeout_seconds=connector.timeout,
            ))
            _connector_names.add(connector.name)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def from_defs(defs: list[Any]) -> list[Connector]:
    """Map validated flow ConnectorDefs to Connector specs."""
    return [
        Connector(
            name=str(d.name),
            description=str(d.description or ""),
            method=str(d.method or "GET").upper(),
            url=str(d.url),
            headers=dict(d.headers or {}),
            body=str(d.body or ""),
            level=PermissionLevel(str(d.level or "read")),
            timeout=float(getattr(d, "timeout", 15.0) or 15.0),
        )
        for d in defs
    ]