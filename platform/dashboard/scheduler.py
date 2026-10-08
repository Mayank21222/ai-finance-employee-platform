"""Phase 5: scheduled background runs.

A daemon thread wakes every CHECK_INTERVAL seconds, asks `check_due()` which
enabled schedules are due, and runs them through the same `runner.start_run`
path as the console. Cron is a deliberately tiny format (no external dep):

    daily 09:30              every day at 09:30 local-to-the-schedule time
    weekly mon 09:30         every Monday at 09:30

A schedule is due when the wall clock's minute-of-day matches the configured
time exactly and the schedule has not run within the last REPEAT_GUARD
seconds (so a check that lands a few seconds late still fires once, and never
twice). `tz_offset` shifts UTC by whole/half hours.

Reliability rules (Improvements.md): the thread catches every exception and
keeps running; when a run is already active the schedule is recorded as a
skipped run with a reason and retried at the next check.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from platform.dashboard import db, runner

CHECK_INTERVAL = 60.0
REPEAT_GUARD = 70.0

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5,
             "sun": 6}

_thread: threading.Thread | None = None
_stop = threading.Event()


def parse_cron(expr: str) -> tuple[str, int | None, int, int] | None:
    """Return (kind, weekday, hour, minute) or None when malformed.

    kind is "daily" or "weekly"; weekday is 0..6 (Mon..Sun) for weekly.
    """
    parts = str(expr or "").strip().lower().split()
    try:
        if len(parts) == 2 and parts[0] == "daily":
            hour, minute = _hhmm(parts[1])
            return "daily", None, hour, minute
        if len(parts) == 3 and parts[0] == "weekly":
            day = _WEEKDAYS.get(parts[1][:3])
            if day is None:
                return None
            hour, minute = _hhmm(parts[2])
            return "weekly", day, hour, minute
    except (ValueError, IndexError):
        return None
    return None


def _hhmm(text: str) -> tuple[int, int]:
    hour_s, minute_s = text.split(":")
    hour, minute = int(hour_s), int(minute_s)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time out of range")
    return hour, minute


def is_due(schedule: dict, now: float) -> bool:
    """Is this schedule due at unix time `now`?"""
    parsed = parse_cron(schedule.get("cron", ""))
    if parsed is None:
        return False
    kind, weekday, hour, minute = parsed
    local = (datetime.fromtimestamp(now, timezone.utc)
             + timedelta(hours=float(schedule.get("tz_offset") or 0.0)))
    if kind == "weekly" and local.weekday() != weekday:
        return False
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (local - target).total_seconds()
    if not (0 <= delta < CHECK_INTERVAL):
        return False
    last = schedule.get("last_run_at")
    if last is not None and (now - float(last)) < REPEAT_GUARD:
        return False
    return True


def check_due(now: float | None = None, start=runner.start_run) -> list[str]:
    """Fire every due, enabled schedule. Returns the ids of runs started (or
    the skipped run ids). Never raises for one bad schedule."""
    now = float(now if now is not None else time.time())
    fired: list[str] = []
    for schedule in db.list_schedules():
        if not schedule.get("enabled"):
            continue
        if not is_due(schedule, now):
            continue
        sid = int(schedule["id"])
        try:
            if runner.active() is not None:
                run_id = runner.new_run_id()
                db.record_skipped_run(
                    run_id, int(schedule["session_id"]), schedule["task"],
                    "another run is active; retry next check",
                    source="schedule", schedule_id=sid,
                )
                db.mark_schedule_run(sid, run_id)
                print(f"[scheduler] schedule {sid} skipped: run active "
                      f"({run_id})")
                fired.append(run_id)
                continue
            handle = start(schedule["task"], int(schedule["session_id"]),
                           source="schedule", schedule_id=sid)
            db.mark_schedule_run(sid, handle.run_id)
            fired.append(handle.run_id)
        except Exception as exc:  # noqa: BLE001 - the thread must never die
            print(f"[scheduler] schedule {sid} failed to start: {exc!r}")
    return fired


def _loop() -> None:
    while not _stop.wait(CHECK_INTERVAL):
        try:
            check_due()
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] check failed: {exc!r}")
        # Phase 6 section 5: event triggers use the same 60-second cadence.
        try:
            from platform.dashboard import triggers

            started = triggers.poll_inbox()
            if started:
                print(f"[scheduler] inbox trigger runs: {', '.join(started)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[scheduler] trigger check failed: {exc!r}")


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()