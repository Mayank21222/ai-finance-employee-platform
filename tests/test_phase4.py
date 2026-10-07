"""Phase 4 additions: usage/config mode, data models, field permissions,
connector registry page, SVG flow editor, audit trail."""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

_SHARED = tempfile.mkdtemp(prefix="phase4_dash_")
os.environ["DASH_DB"] = os.path.join(_SHARED, "dashboard.db")
os.environ["STUB_MODEL"] = "1"

from ai_operator.tools import connectors as connector_tools
from ai_operator.tools.registry import get, names
from platform.dashboard import db
from platform.dashboard import app as dashapp

client = TestClient(dashapp.app)
db.init_db()


def test_mode_toggle_switches_body_attribute_and_persists():
    # Default (no cookie): usage mode - edit surfaces carry mode-edit class.
    resp = client.get("/runs")
    assert 'data-mode="use"' in resp.text
    assert 'href="/mode/configure"' in resp.text  # toggle offers Configure
    assert "mode-edit" in resp.text
    # Configure mode: cookie set, body attribute flips, toggle now readies Use.
    resp = client.get("/mode/configure", headers={"Referer": "/runs"},
                      follow_redirects=False)
    assert resp.status_code == 303
    assert "app_mode=configure" in resp.headers.get("set-cookie", "")
    resp = client.get("/runs")
    assert 'data-mode="configure"' in resp.text
    assert 'href="/mode/use"' in resp.text  # toggle now readies Use mode
    # Run surfaces are mode-run and hidden in configure mode (CSS contract).
    assert 'class="active mode-run"' in resp.text
    resp = client.get("/mode/use", follow_redirects=False)
    assert "app_mode=use" in resp.headers.get("set-cookie", "")
    resp = client.get("/runs")
    assert 'data-mode="use"' in resp.text