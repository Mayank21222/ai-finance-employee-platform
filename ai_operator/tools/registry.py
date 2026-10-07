"""Tool registry: every tool is a spec (name, permission level, typed args, timeout).

Dispatch validates arguments with Pydantic before the handler runs. The model
can only ever reference tools that exist here; unknown names are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ai_operator.permissions import PermissionLevel


class UnknownToolError(KeyError):
    """Raised when a tool name is not in the registry."""


class ToolResult(BaseModel):
    ok: bool
    output: str
    attempts: int = 1
    retryable: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    level: PermissionLevel
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], str]
    timeout_seconds: float = 15.0

    def to_prompt_line(self) -> str:
        fields = ", ".join(self.args_model.model_fields.keys()) or "(no args)"
        return f"- {self.name}({fields}) [{self.level.value}] — {self.description}"


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"tool already registered: {spec.name}")
    _REGISTRY[spec.name] = spec
    return spec


def tool(
    name: str,
    description: str,
    level: PermissionLevel,
    args_model: type[BaseModel],
    timeout_seconds: float | None = None,
) -> Callable[[Callable[[BaseModel], str]], Callable[[BaseModel], str]]:
    def decorator(fn: Callable[[BaseModel], str]) -> Callable[[BaseModel], str]:
        timeout = timeout_seconds if timeout_seconds is not None else _default_timeout()
        register(ToolSpec(name, description, level, args_model, fn, timeout))
        return fn

    return decorator


def _default_timeout() -> float:
    import os

    return float(os.environ.get("TOOL_TIMEOUT_SECONDS", "15"))


def get(name: str) -> ToolSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownToolError(name) from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def describe_for_prompt() -> str:
    return "\n".join(_REGISTRY[n].to_prompt_line() for n in sorted(_REGISTRY))


def run(name: str, args: dict[str, Any]) -> ToolResult:
    """Validate args, execute, and capture errors as observations.

    Never raises for handler failures: the agent must see the error text.
    """
    spec = get(name)
    try:
        typed = spec.args_model.model_validate(args)
    except ValidationError as exc:
        return ToolResult(ok=False, output=f"INVALID_ARGUMENTS: {exc.errors()}")
    try:
        output = spec.handler(typed)
        return ToolResult(ok=True, output=output)
    except TimeoutError as exc:
        return ToolResult(ok=False, output=f"TIMEOUT: {exc}", retryable=True)
    except ConnectionError as exc:
        return ToolResult(ok=False, output=f"CONNECTION_ERROR: {exc}", retryable=True)
    except Exception as exc:  # noqa: BLE001 - errors are observations for the model
        return ToolResult(
            ok=False, output=f"ERROR ({type(exc).__name__}): {exc}", retryable=False
        )


def clear_registry() -> None:
    """Test hook."""
    _REGISTRY.clear()


def unregister(name: str) -> None:
    """Remove a tool; used by the connector registry to re-sync (Phase 3)."""
    _REGISTRY.pop(name, None)
