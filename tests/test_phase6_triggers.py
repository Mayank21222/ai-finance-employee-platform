"""Phase 6 section 5: event triggers (inbox folder + webhook).

Mandated coverage:
  * a file dropped into the inbox fires exactly one run and is moved to
    processed;
  * while a run is active the file stays in the inbox and skipped_reason is
    recorded;
  * a webhook with a wrong secret returns 403 and fires nothing (a correct
    one starts a run through the shared start_run path).
"""

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

_TMP = Path(tempfile.mkdtemp(prefix="phase6_trg_"))
os.environ.setdefault("DASH_DB", str(_TMP / "dashboard.db"))
os.environ["STUB_MODEL"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from platform.dashboard import db, runner, triggers  # noqa: E402
from platform.dashboard.app import app  # noqa: E402

db.init_db()
client = TestClient(app)


def _delete_runs_matching(prefix: str) -> None:
    with db.connect() as con:
        con.execute("DELETE FROM runs WHERE run_id LIKE ?", (prefix + "%",))


def test_inbox_file_fires_exactly_one_run_and_moves_to_processed():
    inbox = _TMP / "inbox_one"
    shutil.rmtree(inbox, ignore_errors=True)
    inbox.mkdir(parents=True)
    (inbox / "new_invoice.txt").write_text("file body", encoding="utf-8")
    tid = db.add_trigger(1, "inbox_folder", {"path": str(inbox)},
                         "process {{file.name}}")
    started: list[tuple[str, str]] = []

    def fake_start(task, session_id, source="manual", schedule_id=None,
                   **kwargs):
        started.append(("trg_fire_1", task))
        db.record_run("trg_fire_1", session_id, task, source=source)
        return SimpleNamespace(run_id="trg_fire_1")

    try:
        fired = triggers.poll_inbox(start=fake_start, active=lambda: None)
        assert fired == ["trg_fire_1"]
        row = db.get_run("trg_fire_1")
        assert row["source"] == "trigger"
        assert row["task"] == "process new_invoice.txt"
        # exactly one run: the file moved to processed/ and stays there
        assert not (inbox / "new_invoice.txt").exists()
        assert (inbox / "processed" / "new_invoice.txt").is_file()
        fired_again = triggers.poll_inbox(start=fake_start, active=lambda: None)
        assert fired_again == []
        assert started == [("trg_fire_1", "process new_invoice.txt")]
        # the trigger remembers what it fired
        assert db.get_trigger(tid)["last_run_id"] == "trg_fire_1"
    finally:
        db.delete_trigger(tid)
        _delete_runs_matching("trg_fire_")
        shutil.rmtree(inbox, ignore_errors=True)


def test_inbox_file_left_in_place_while_a_run_is_active():
    inbox = _TMP / "inbox_busy"
    shutil.rmtree(inbox, ignore_errors=True)
    inbox.mkdir(parents=True)
    (inbox / "stuck.txt").write_text("later", encoding="utf-8")
    tid = db.add_trigger(1, "inbox_folder", {"path": str(inbox)},
                         "handle {{file.name}}")

    def must_not_start(*args, **kwargs):
        raise AssertionError("must not start a second concurrent run")

    try:
        fired = triggers.poll_inbox(
            start=must_not_start, active=lambda: SimpleNamespace(run_id="busy"))
        assert fired == []
        # file left in the inbox, and the skip is recorded with a reason
        assert (inbox / "stuck.txt").is_file()
        skipped = [r for r in db.list_runs(200)
                   if r.get("source") == "trigger"
                   and r.get("skipped_reason")
                   and r.get("task") == "handle stuck.txt"]
        assert skipped, "the skip must be recorded in the runs table"
        assert "active" in skipped[0]["skipped_reason"]
        assert skipped[0]["state"] == "skipped"
    finally:
        db.delete_trigger(tid)
        with db.connect() as con:
            con.execute("DELETE FROM runs WHERE source = 'trigger' "
                        "AND task = 'handle stuck.txt'")
        shutil.rmtree(inbox, ignore_errors=True)


def test_webhook_wrong_secret_fires_nothing_and_right_secret_starts(monkeypatch):
    tid = db.add_trigger(1, "webhook", {}, "ping {{note}}",
                         secret="correct-horse")
    before = len(db.list_runs(500))

    def fake_start(task, session_id, source="manual", schedule_id=None,
                   **kwargs):
        db.record_run("trg_wh_1", session_id, task, source=source)
        return SimpleNamespace(run_id="trg_wh_1")

    monkeypatch.setattr(runner, "start_run", fake_start)
    monkeypatch.setattr(runner, "active", lambda: None)
    try:
        resp = client.post(f"/api/v1/triggers/{tid}/fire",
                           headers={"X-Trigger-Secret": "wrong"},
                           json={"note": "urgent"})
        assert resp.status_code == 403
        assert "secret" in resp.json()["detail"]
        assert len(db.list_runs(500)) == before  # nothing fired
        assert db.get_trigger(tid)["last_run_id"] is None

        resp = client.post(f"/api/v1/triggers/{tid}/fire",
                           headers={"X-Trigger-Secret": "correct-horse"},
                           json={"note": "urgent"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "started"
        assert body["run_id"] == "trg_wh_1"
        assert body["task"] == "ping urgent"  # {{note}} from the body
        row = db.get_run("trg_wh_1")
        assert row["source"] == "trigger"
        assert db.get_trigger(tid)["last_run_id"] == "trg_wh_1"

        # unknown trigger id -> 404, not a fire
        assert client.post("/api/v1/triggers/99999/fire",
                           headers={"X-Trigger-Secret": "x"}).status_code == 404
    finally:
        db.delete_trigger(tid)
        _delete_runs_matching("trg_wh_%")


def test_triggers_page_renders_and_nav_links():
    tid = db.add_trigger(1, "webhook", {}, "hello", secret="abcdef123456")
    try:
        body = client.get("/triggers").text
        assert "Event triggers" in body
        assert str(tid) in body and "X-Trigger-Secret" in body
        assert "abcd…" in body  # secret masked, never shown in full
        # NAV link present in the shell
        assert 'href="/triggers"' in client.get("/").text
        # enable/disable toggle round trip
        assert client.post(f"/triggers/{tid}/toggle",
                           follow_redirects=False).status_code == 303
        assert not db.get_trigger(tid)["enabled"]
        assert client.post(f"/triggers/{tid}/delete",
                           follow_redirects=False).status_code == 303
        assert db.get_trigger(tid) is None
    finally:
        db.delete_trigger(tid)
