"""Tool registry: registration, unknown names, argument validation, error capture."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from ai_operator.permissions import PermissionLevel
from ai_operator.tools import registry


class NoArgs(BaseModel):
    pass


def _spec(name: str, handler: Any) -> registry.ToolSpec:
    return registry.ToolSpec(
        name=name, description="test tool", level=PermissionLevel.read,
        args_model=NoArgs, handler=handler,
    )


def test_core_tools_are_registered():
    expected = {
        "search_files", "read_file", "navigate", "read_page", "fill", "click",
        "submit_form", "screenshot", "read_memory", "write_memory",
    }
    assert expected <= set(registry.names())


def test_unknown_tool_raises():
    with pytest.raises(registry.UnknownToolError):
        registry.get("definitely_not_a_tool")


def test_duplicate_registration_rejected():
    registry.register(_spec("tmp_duplicate_probe", lambda _: "x"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_spec("tmp_duplicate_probe", lambda _: "y"))


def test_invalid_arguments_are_captured_not_raised():
    result = registry.run("fill", {})
    assert result.ok is False
    assert "INVALID_ARGUMENTS" in result.output


def test_handler_exception_becomes_observation():
    def boom(_: BaseModel) -> str:
        raise RuntimeError("kaput")

    registry.register(_spec("tmp_boom_tool", boom))
    result = registry.run("tmp_boom_tool", {})
    assert result.ok is False
    assert result.output.startswith("ERROR (RuntimeError): kaput")
    assert result.retryable is False


def test_timeout_is_retryable():
    def slow(_: BaseModel) -> str:
        raise TimeoutError("too slow")

    registry.register(_spec("tmp_slow_tool", slow))
    result = registry.run("tmp_slow_tool", {})
    assert result.ok is False
    assert result.output.startswith("TIMEOUT:")
    assert result.retryable is True


def test_connection_error_is_retryable():
    def down(_: BaseModel) -> str:
        raise ConnectionError("refused")

    registry.register(_spec("tmp_down_tool", down))
    result = registry.run("tmp_down_tool", {})
    assert result.ok is False
    assert result.output.startswith("CONNECTION_ERROR:")
    assert result.retryable is True


def test_describe_for_prompt_lists_every_tool():
    text = registry.describe_for_prompt()
    assert "- search_files(" in text
    assert text.count("\n") + 1 == len(registry.names())
