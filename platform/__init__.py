"""Low-code platform layer: flow configs, compiler, and dashboard.

Package name note: this directory intentionally shadows the stdlib module of
the same name (the Improvements spec mandates platform/flow and
platform/dashboard). To keep `import platform` working for pytest, playwright
and anything else that expects the stdlib module, this __init__ executes the
real stdlib platform.py source into our namespace first, then the platform.*
submodules attach on top. Tests cover this (test_flow_validator imports the
package in a fresh interpreter).
"""

from __future__ import annotations

import pathlib as _pathlib
import sysconfig as _sysconfig

def _load_stdlib_platform() -> None:
    _path = _pathlib.Path(_sysconfig.get_paths()["stdlib"]) / "platform.py"
    if _path.is_file():
        _src = _path.read_text(encoding="utf-8")
        exec(compile(_src, str(_path), "exec"), globals())


_load_stdlib_platform()
