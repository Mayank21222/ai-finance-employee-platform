"""Dashboard skeleton: sessions CRUD, runs shell, static assets."""

import os
import tempfile
from pathlib import Path

os.environ["DASH_DB"] = str(Path(tempfile.mkdtemp()) / "dash_test.db")

from fastapi.testclient import TestClient  # noqa: E402

from platform.dashboard import db  # noqa: E402
from platform.dashboard.app import app  # noqa: E402

db.init_db()  # TestClient without a lifespan context does not run startup hooks
client = TestClient(app)


def test_root_redirects_to_run_console():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/runs"


def test_run_console_renders_with_session_dropdown():
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert "Run console" in resp.text
    assert "acme" in resp.text  # seeded default session


def test_sessions_page_lists_seed_and_form():
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert "Sessions" in resp.text and "acme" in resp.text
    assert 'action="/sessions"' in resp.text


def test_session_create_edit_and_delete_cycle():
    resp = client.post("/sessions", data={
        "tenant": "globex", "currency": "EUR",
        "approval_threshold": "1200.5", "user_role": "viewer",
    }, follow_redirects=False)
    assert resp.status_code == 303
    listing = client.get("/sessions").text
    assert "globex" in listing and "1200.5" in listing

    # find the new session id from the delete form
    import re
    ids = [int(m) for m in re.findall(r'/sessions/(\d+)/delete', listing)]
    assert len(ids) == 2
    resp = client.post(f"/sessions/{ids[-1]}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert "globex" not in client.get("/sessions").text


def test_session_create_rejects_bad_threshold():
    resp = client.post("/sessions", data={
        "tenant": "x", "currency": "USD",
        "approval_threshold": "not-a-number", "user_role": "op",
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert "must be a number" in resp.text


def test_htmx_asset_served():
    resp = client.get("/static/htmx.min.js")
    assert resp.status_code == 200
    assert "htmx" in resp.text
