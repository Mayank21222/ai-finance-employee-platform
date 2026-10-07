"""File tools: search and read inside the sandboxed workspace."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.registry import tool

WORKSPACE = Path(__file__).resolve().parents[2]
MAX_READ_CHARS = 8000
MAX_RESULTS = 25


class SearchFilesArgs(BaseModel):
    query: str = Field(description="substring or simple term to look for")
    path: str = Field(default="company_data", description="directory to search, relative to repo root")


class ReadFileArgs(BaseModel):
    path: str = Field(description="file path relative to the repo root")


def _resolve(relative: str) -> Path:
    candidate = (WORKSPACE / relative).resolve()
    if not str(candidate).startswith(str(WORKSPACE)):
        raise PermissionError(f"path escapes the sandbox: {relative}")
    if not candidate.is_file():
        raise FileNotFoundError(f"no such file: {relative}")
    return candidate


@tool("search_files", "Search text files under a directory for a term.",
      PermissionLevel.read, SearchFilesArgs)
def search_files(args: SearchFilesArgs) -> str:
    root = (WORKSPACE / args.path).resolve()
    if not str(root).startswith(str(WORKSPACE)):
        raise PermissionError(f"path escapes the sandbox: {args.path}")
    if not root.exists():
        raise FileNotFoundError(f"no such directory: {args.path}")
    hits: list[str] = []
    needle = args.query.lower()
    for file in sorted(root.rglob("*")):
        if not file.is_file() or file.suffix in {".db", ".png", ".pyc"}:
            continue
        try:
            if needle in file.read_text(errors="ignore").lower():
                hits.append(str(file.relative_to(WORKSPACE)))
        except OSError:
            continue
        if len(hits) >= MAX_RESULTS:
            break
    if not hits:
        return f"no files matched query={args.query!r} under {args.path}"
    return "\n".join(hits)


@tool("read_file", "Read a text file and return its contents.",
      PermissionLevel.read, ReadFileArgs)
def read_file(args: ReadFileArgs) -> str:
    target = _resolve(args.path)
    text = target.read_text(errors="replace")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n... [truncated at {MAX_READ_CHARS} chars]"
    return text
