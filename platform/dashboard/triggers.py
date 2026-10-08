"""Phase 6 section 5: event triggers - "something happens" runs.

Two trigger types, both flowing through the same `runner.start_run` path and
the same one-run-at-a-time rule as the console and schedules:

* ``inbox_folder`` - the scheduler thread polls a folder every 60s; a new
  file starts a run from the task template (``{{file.name}}``,
  ``{{file.path}}``). The file moves to ``inbox/processed/`` once the run
  starts, or to ``inbox/failed/`` when its run ends badly. While another run
  is active the file stays in the inbox and a skipped run row records why.
* ``webhook`` - ``POST /api/v1/triggers/{id}/fire`` authenticated with the
  trigger's own secret header (403 on a wrong secret, nothing fires); the
  JSON body supplies template values.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from platform.dashboard import db, runner

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INBOX = REPO_ROOT / "company_data" / "acme" / "inbox"
SKIP_DEDUP_SECONDS = 300.0


def render_template(template: str, values: dict[str, Any]) -> str:
    """Replace ``{{key}}`` placeholders (literal keys, e.g. file.name)."""
    out = str(template)
    for key, value in values.items():
        out = out.replace("{{" + str(key) + "}}", str(value))
        out = out.replace("{{ " + str(key) + " }}", str(value))
    return out


def _inbox_path(trigger: dict[str, Any]) -> Path:
    raw = str((trigger.get("config") or {}).get("path") or "").strip()
    path = Path(raw) if raw else DEFAULT_INBOX
    return path if path.is_absolute() else REPO_ROOT / path


def _record_skip(trigger: dict[str, Any], task: str, reason: str) -> str:
    """Record a skipped trigger run, deduplicated per task per few minutes."""
    cutoff = time.time() - SKIP_DEDUP_SECONDS
    for row in db.list_runs(200):
        if (row.get("source") == "trigger" and row.get("skipped_reason")
                and row.get("task") == task
                and float(row.get("created_at") or 0) >= cutoff):
            return str(row["run_id"])
    run_id = runner.new_run_id()
    db.record_skipped_run(run_id, int(trigger["session_id"]), task, reason,
                          source="trigger")
    return run_id


def _settle_finished_file(trigger: dict[str, Any]) -> None:
    """processed/<file> -> failed/<file> when the firing run ended badly."""
    last_path = trigger.get("last_file_path")
    if not last_path:
        return
    run_id = trigger.get("last_run_id")
    run = db.get_run(str(run_id)) if run_id else None
    if run is None or not run.get("finished_at"):
        return  # still running (or history pruned): settle on a later check
    path = Path(str(last_path))
    if path.is_file() and str(run.get("status")) in ("failed", "needs_human"):
        failed_dir = path.parent.parent / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / path.name
        if not dest.exists():
            path.rename(dest)
    db.clear_trigger_file(int(trigger["id"]))


def poll_inbox(
    start: Callable[..., Any] = None,
    active: Callable[[], Any] = None,
) -> list[str]:
    """One poll of every enabled inbox trigger. Returns started run ids.

    At most one run per trigger per poll (one run at a time); the next file
    is picked up by the next check.
    """
    start = start or runner.start_run   # late binding: monkeypatch-friendly
    active = active or runner.active
    fired: list[str] = []
    for trigger in db.list_triggers():
        if not trigger.get("enabled") or trigger.get("type") != "inbox_folder":
            continue
        _settle_finished_file(trigger)
        inbox = _inbox_path(trigger)
        if not inbox.is_dir():
            continue
        files = sorted(p for p in inbox.iterdir() if p.is_file())
        if not files:
            continue
        file = files[0]
        task = render_template(
            trigger.get("task_template") or "process {{file.name}}",
            {"file.name": file.name, "file.path": str(file)})
        if active() is not None:
            _record_skip(trigger, task,
                         "another run is active; file left in inbox")
            continue  # file stays put: retried on the next check
        try:
            handle = start(task, int(trigger["session_id"]),
                           source="trigger")
        except Exception as exc:  # noqa: BLE001 - never kill the poll loop
            _record_skip(trigger, task, f"could not start run: {exc}")
            continue
        processed = inbox / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        dest = processed / file.name
        if dest.exists():  # never overwrite a previously processed file
            dest = processed / f"{int(time.time())}_{file.name}"
        file.rename(dest)
        db.mark_trigger_fired(int(trigger["id"]), str(handle.run_id),
                              str(dest))
        fired.append(str(handle.run_id))
    return fired


def fire_webhook(trigger_id: int, body: dict[str, Any], secret: str,
                 start: Callable[..., Any] = None,
                 active: Callable[[], Any] = None) -> tuple[int, dict]:
    """Authenticate and fire a webhook trigger. Returns (http_status, body)."""
    start = start or runner.start_run
    active = active or runner.active
    trigger = db.get_trigger(trigger_id)
    if trigger is None or trigger.get("type") != "webhook":
        return 404, {"detail": f"no webhook trigger {trigger_id}"}
    if not secret or secret != str(trigger.get("secret") or ""):
        return 403, {"detail": "wrong trigger secret"}
    if not trigger.get("enabled"):
        return 409, {"detail": "trigger is disabled"}
    task = render_template(trigger.get("task_template") or "",
                           dict(body or {}))
    if active() is not None:
        reason = "another run is active; webhook recorded as skipped"
        run_id = _record_skip(trigger, task, reason)
        return 200, {"status": "skipped", "reason": reason,
                     "run_id": run_id}
    try:
        handle = start(task, int(trigger["session_id"]), source="trigger")
    except Exception as exc:  # noqa: BLE001
        run_id = _record_skip(trigger, task, f"could not start run: {exc}")
        return 200, {"status": "skipped", "reason": str(exc),
                     "run_id": run_id}
    db.mark_trigger_fired(int(trigger["id"]), str(handle.run_id))
    return 200, {"status": "started", "run_id": str(handle.run_id),
                 "task": task}
