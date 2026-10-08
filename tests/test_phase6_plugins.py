"""Phase 6 section 8: microkernel-style plugin registration (small slice).

Fixture plugin files are written into plugins/ and removed again by the
fixture; nothing here touches the network or any real service.
"""

import os
import tempfile
import textwrap
from pathlib import Path

os.environ.setdefault("DASH_DB",
                      str(Path(tempfile.mkdtemp()) / "dash_plugins.db"))

import pytest  # noqa: E402

from ai_operator.tools import registry  # noqa: E402
from platform.dashboard import db, plugins  # noqa: E402
from platform.dashboard.app import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

db.init_db()
client = TestClient(app)

PLUGINS_DIR = plugins.PLUGINS_DIR

FIXTURE_TOOL_PLUGIN = textwrap.dedent('''
    from platform.dashboard.plugins import PluginManifest
    from ai_operator.permissions import PermissionLevel
    from ai_operator.tools.registry import ToolSpec
    from ai_operator.tools.registry import register as register_tool
    from pydantic import BaseModel

    MANIFEST = PluginManifest(
        name="fixture-tools",
        version="1.2.3",
        description="a test plugin that adds one tool",
        provides=("tool:fixture_widget", "page:/flow"),
    )

    class _Args(BaseModel):
        pass

    def register():
        register_tool(ToolSpec(
            name="fixture_widget",
            description="returns a canned string (test plugin)",
            level=PermissionLevel.read,
            args_model=_Args,
            handler=lambda args: "fixture ok",
        ))
        return []
''')

FIXTURE_BAD_REGISTER = textwrap.dedent('''
    from platform.dashboard.plugins import PluginManifest

    MANIFEST = PluginManifest(name="bad-reg", version="0.1.0")

    def register():
        raise RuntimeError("register exploded")
''')


@pytest.fixture()
def clean_plugins():
    yield
    for path in list(PLUGINS_DIR.glob("fixture_*.py")) + \
            list(PLUGINS_DIR.glob("broken_*.py")) + \
            list(PLUGINS_DIR.glob("bad_*.py")):
        path.unlink(missing_ok=True)
    for name in registry.names():
        if name.startswith("fixture_"):
            registry.unregister(name)
    plugins.load_all()


def test_fixture_plugin_tool_appears_in_registry_and_agent_toggles(
        clean_plugins):
    (PLUGINS_DIR / "fixture_tools.py").write_text(FIXTURE_TOOL_PLUGIN)
    loaded = plugins.load_all()
    entry = next(p for p in loaded if p.manifest.name == "fixture-tools")
    assert entry.status == "loaded"
    assert entry.source == "fixture_tools.py"
    assert entry.manifest.version == "1.2.3"
    # the tool is registered and actually runs
    assert "fixture_widget" in registry.names()
    res = registry.run("fixture_widget", {})
    assert res.ok and res.output == "fixture ok"
    # it shows up in the agent tool toggles (node edit panel badges)
    page = client.get("/flow/nodes/ap_agent")
    assert page.status_code == 200
    assert "fixture_widget" in page.text
    # and on the read-only plugins page, with what it provides
    body = client.get("/plugins").text
    assert "fixture-tools" in body
    assert "tool:fixture_widget" in body
    assert "1.2.3" in body


def test_plugin_raising_in_register_is_skipped(clean_plugins):
    (PLUGINS_DIR / "bad_register.py").write_text(FIXTURE_BAD_REGISTER)
    loaded = plugins.load_all()
    entry = next(p for p in loaded if p.manifest.name == "bad-reg")
    assert entry.status == "skipped"
    assert "register exploded" in entry.error
    # the rest still loaded
    assert any(p.manifest.name == "connectors" and p.status == "loaded"
               for p in loaded)


def test_broken_import_does_not_stop_the_dashboard(clean_plugins):
    (PLUGINS_DIR / "broken_import.py").write_text(
        "raise RuntimeError('boom at import')\n")
    # a lifespan-running client executes startup, which must not raise
    with TestClient(app) as started:
        resp = started.get("/plugins")
        assert resp.status_code == 200
        assert "broken_import" in resp.text
        assert "skipped" in resp.text
        assert "boom at import" in resp.text
        # the dashboard is fully usable alongside the skipped plugin
        assert started.get("/runs").status_code == 200


def test_connectors_loader_is_a_builtin_plugin(clean_plugins):
    from ai_operator.tools import connectors as connector_tools

    loaded = plugins.load_all()
    entry = next(p for p in loaded if p.manifest.name == "connectors")
    assert entry.source == "builtin"
    assert entry.status == "loaded"
    assert "page:/connectors" in entry.manifest.provides
    # the loader really published: connector tools are in the registry
    assert connector_tools.registered_names()


def test_plugins_page_is_read_only(clean_plugins):
    plugins.load_all()
    resp = client.get("/plugins")
    assert resp.status_code == 200
    assert "registers through one" in resp.text
    assert "connectors" in resp.text
    # read-only: the page renders no forms at all
    assert "<form" not in resp.text
    # the NAV entry is present in configuration-mode pages
    assert 'href="/plugins"' in resp.text
