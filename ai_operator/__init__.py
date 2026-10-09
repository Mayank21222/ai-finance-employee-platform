"""Single-agent LangGraph operator core for the AI Finance Employee platform."""

import os

__version__ = "0.1.0"


def _load_env(path: str = ".env") -> None:
    """Load KEY=VALUE lines from `.env` into os.environ (real env wins).

    Keeps secrets out of the code and out of git while letting the operator,
    dashboard and CLI pick them up with a plain os.getenv().

    Inline comments after a value are stripped, and values may be wrapped in
    single or double quotes. A bare key with no `=` is ignored (a comment line
    is already skipped above)."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key or not key.isidentifier():
                    continue
                value = value.strip()
                # strip an inline comment: a `#` that starts a word (preceded by
                # whitespace or at the start) begins a comment. Quoted values
                # keep their `#` — only strip when it is not inside quotes.
                if value and not (value[0] in "\"'"):
                    hash_idx = value.find(" #")
                    if hash_idx != -1:
                        value = value[:hash_idx].strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
    except OSError:
        pass


_load_env()