"""Phase 6 section 8: microkernel-style plugin registration (small slice).

NocoBase's rule: "everything is a plugin and the system can grow without
losing control." This is the smallest useful slice of that: new tools (and
optionally node types / dashboard pages) register through ONE interface -
a ``PluginManifest`` plus a ``register()`` function - instead of edits to
core files.

* Built-in plugins live in ``BUILTIN_PLUGINS``. The **connector tool
  loader** (Phase 3/4) is declared there as proof: the same
  ``flowcfg.publish_connectors()`` the dashboard has always called is now
  invoked through the generic plugin interface at startup. Nothing else was
  moved.
* External plugins are ``plugins/*.py`` files at the repo root, each
  defining a module-level ``MANIFEST = PluginManifest(...)`` and an optional
  ``register() -> list[str]`` (returning warning strings). A plugin may
  register tools with the normal ``ai_operator.tools.registry`` API.

Failure policy: a plugin that raises on import or during register() is
**logged and skipped** - it can never stop the dashboard from starting.
``load_all()`` records every outcome in ``list_plugins()`` for the read-only
plugins page.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_DIR = REPO_ROOT / "plugins"


@dataclass(frozen=True)
class PluginManifest:
    """What a plugin declares about itself (all display-only)."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    provides: tuple[str, ...] = field(default_factory=tuple)
    """Examples: ``tool:get_invoice_list``, ``node_types``, ``page:/flow``."""


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    source: str          # "builtin" or the file name in plugins/
    status: str          # "loaded" | "skipped"
    error: str = ""      # skip reason, or non-fatal loader warnings

    def as_dict(self) -> dict:
        return {"name": self.manifest.name,
                "version": self.manifest.version,
                "description": self.manifest.description,
                "provides": list(self.manifest.provides),
                "source": self.source,
                "status": self.status,
                "error": self.error}


_loaded: dict[str, LoadedPlugin] = {}


def list_plugins() -> list[LoadedPlugin]:
    return list(_loaded.values())


# --- built-in plugins -------------------------------------------------------


def _load_connectors() -> tuple[PluginManifest, list[str]]:
    """The Phase 3/4 connector tool loader, behind the plugin interface."""
    from platform.dashboard import flowcfg
    from ai_operator.tools import connectors as connector_tools

    errors = flowcfg.publish_connectors()
    provides = ["page:/connectors"]
    provides += [f"tool:{name}"
                 for name in sorted(connector_tools.registered_names())]
    provides += ["data_sources", "saved_queries"]
    return (PluginManifest(
        name="connectors",
        version="1.0.0",
        description="Connector tool loader: DB registry -> flow config -> "
                    "registry, plus read-only data-source queries "
                    "(Phase 3/4/6).",
        provides=tuple(provides),
    ), list(errors))


BUILTIN_PLUGINS: list[tuple[str, Callable[[],
                                          tuple[PluginManifest, list[str]]]]] = [
    ("builtin:connectors", _load_connectors),
]


# --- loading ----------------------------------------------------------------


def _load_builtin(key: str,
                  loader: Callable[[], tuple[PluginManifest, list[str]]]
                  ) -> None:
    try:
        manifest, errors = loader()
        _loaded[manifest.name] = LoadedPlugin(
            manifest, "builtin", "loaded",
            "; ".join(errors))
        if errors:
            print(f"[plugins] {manifest.name}: {'; '.join(errors)}")
    except Exception as exc:  # noqa: BLE001 - a builtin must not kill startup
        _loaded[key] = LoadedPlugin(
            PluginManifest(name=key, description="built-in plugin"),
            "builtin", "skipped", f"{type(exc).__name__}: {exc}")
        print(f"[plugins] built-in {key} skipped: {exc!r}")


def _load_file(path: Path) -> None:
    module_name = f"comp_ops_plugin_{path.stem}"
    manifest: PluginManifest | None = None
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path.name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # may raise: that's the skip path
        manifest = getattr(module, "MANIFEST", None)
        if not isinstance(manifest, PluginManifest):
            raise ValueError("no module-level MANIFEST = PluginManifest(...)")
        warnings: list[str] = []
        register = getattr(module, "register", None)
        if callable(register):
            result = register()
            if result:
                warnings = [str(w) for w in result]
        _loaded[manifest.name] = LoadedPlugin(
            manifest, path.name, "loaded", "; ".join(warnings))
    except Exception as exc:  # noqa: BLE001 - log, skip, keep the app alive
        sys.modules.pop(module_name, None)
        # keep the real manifest when it was parsed before the failure
        if not isinstance(manifest, PluginManifest):
            manifest = PluginManifest(name=path.stem,
                                      description="(failed to load)")
        _loaded[manifest.name] = LoadedPlugin(
            manifest, path.name, "skipped", f"{type(exc).__name__}: {exc}")
        print(f"[plugins] skipped {path.name}: {exc!r}")
        traceback.print_exc()


def load_all() -> list[LoadedPlugin]:
    """Scan built-ins then plugins/*.py. Never raises; failures are recorded."""
    _loaded.clear()
    for key, loader in BUILTIN_PLUGINS:
        _load_builtin(key, loader)
    if PLUGINS_DIR.is_dir():
        for path in sorted(PLUGINS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            _load_file(path)
    return list_plugins()
