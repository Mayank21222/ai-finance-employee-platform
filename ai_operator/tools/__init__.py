"""Tool package: importing it registers every tool in the registry."""

from ai_operator.tools import browser, files, memory  # noqa: F401
from ai_operator.tools.registry import (  # noqa: F401
    ToolResult,
    ToolSpec,
    UnknownToolError,
    describe_for_prompt,
    get,
    names,
    register,
    run,
    tool,
)

__all__ = [
    "ToolResult",
    "ToolSpec",
    "UnknownToolError",
    "browser",
    "describe_for_prompt",
    "files",
    "get",
    "memory",
    "names",
    "register",
    "run",
    "tool",
]
