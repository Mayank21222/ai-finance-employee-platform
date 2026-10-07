"""Persistent company memory: a JSON document in company_data/."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.files import WORKSPACE
from ai_operator.tools.registry import tool

MEMORY_PATH = WORKSPACE / "company_data" / "memory.json"


class ReadMemoryArgs(BaseModel):
    key: str = Field(default="", description="top-level key to read; empty reads everything")


class WriteMemoryArgs(BaseModel):
    key: str = Field(description="top-level key to write under")
    value: str = Field(description="string value to store")


def _load() -> dict:
    if not MEMORY_PATH.exists():
        return {}
    return json.loads(MEMORY_PATH.read_text())


def _save(data: dict) -> None:
    MEMORY_PATH.write_text(json.dumps(data, indent=2) + "\n")


@tool("read_memory", "Read the persistent company memory (all or one key).",
      PermissionLevel.read, ReadMemoryArgs)
def read_memory(args: ReadMemoryArgs) -> str:
    data = _load()
    if args.key:
        if args.key not in data:
            return f"memory has no key {args.key!r}; keys: {sorted(data)}"
        return json.dumps(data[args.key], indent=2)
    return json.dumps(data, indent=2)


@tool("write_memory", "Write a value into company memory (reversible; old value is returned).",
      PermissionLevel.reversible_write, WriteMemoryArgs)
def write_memory(args: WriteMemoryArgs) -> str:
    data = _load()
    old = data.get(args.key)
    data[args.key] = args.value
    _save(data)
    return f"stored memory[{args.key!r}] (previous value: {old!r})"
