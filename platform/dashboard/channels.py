"""Phase 6 section 6: approval and alert channels.

A channel is an outbound notification destination of one of two types:

* ``webhook``     - a Slack/Teams-style incoming webhook; we POST a JSON
                    body whose ``text`` field carries the message.
* ``email_smtp``  - a plain SMTP server; we send one text/plain message.

Notifications fire on three run-state transitions, always through the same
``db.runs.state`` transitions the runner already performs:

* entering ``waiting_approval`` - action summary, the policy rule that fired,
  and a **signed one-time link** to the approval card;
* ending ``failed`` or ``interrupted`` - what happened and where to look.

The one-time link: ``issue_link`` mints ``nonce.expiry.hmac`` where the HMAC
covers nonce+expiry under a per-workspace secret (env ``APPROVAL_LINK_SECRET``
or a random value persisted in the settings table). Opening the link marks it
used in the DB, so a second open is rejected as "reused" and an old one as
"expired". The link only *opens the card* - approving still goes through
``POST /runs/{id}/answer`` and the role check from section 1, which the link
cannot bypass.

Delivery failures never propagate: the notification row stays behind with
``status='failed'`` and the error text, and the run continues. Nothing here
claims real delivery - see KNOWN_LIMITATIONS for what has been exercised.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import smtplib
import time
from email.message import EmailMessage
from typing import Any

import httpx

from platform.dashboard import db

CHANNEL_TYPES = ("webhook", "email_smtp")
LINK_TTL_SECONDS = 3600.0
SEND_TIMEOUT = 3.0


def base_url() -> str:
    return os.environ.get("DASHBOARD_BASE_URL",
                          "http://127.0.0.1:8001").rstrip("/")


# --- signed one-time approval links ----------------------------------------


def _link_secret() -> str:
    secret = (os.environ.get("APPROVAL_LINK_SECRET")
              or db.get_setting("approval_link_secret"))
    if not secret:
        secret = secrets.token_hex(32)
        db.set_setting("approval_link_secret", secret)
    return secret


def _sign(nonce: str, expiry: float) -> str:
    msg = f"{nonce}.{int(expiry)}".encode()
    return hmac.new(_link_secret().encode(), msg,
                    hashlib.sha256).hexdigest()[:32]


def issue_link(run_id: str, ttl: float = LINK_TTL_SECONDS) -> str:
    """Mint a signed link token valid for `ttl` seconds and one open."""
    nonce = secrets.token_urlsafe(24)
    expiry = time.time() + float(ttl)
    db.record_approval_link(run_id, nonce, expiry)
    return f"{nonce}.{int(expiry)}.{_sign(nonce, expiry)}"


def consume_link(token: str) -> tuple[str, str | None]:
    """Open a one-time link. Returns (status, run_id).

    status is ``ok`` | ``expired`` | ``reused`` | ``invalid``. Exactly one
    open ever succeeds; the DB row is the one-time gate, the HMAC is the
    signature check.
    """
    try:
        nonce, expiry_s, sig = str(token).split(".")
        expiry = float(expiry_s)
    except (ValueError, AttributeError):
        return "invalid", None
    if not hmac.compare_digest(_sign(nonce, expiry), sig):
        return "invalid", None
    row = db.approval_link(nonce)
    if row is None:
        return "invalid", None
    if time.time() > float(row["expires_at"]):
        return "expired", None
    if bool(row["used"]):
        return "reused", None
    if not db.consume_approval_link(nonce):
        return "reused", None
    return "ok", str(row["run_id"])


# --- message construction ---------------------------------------------------


def approval_summary(interrupt: dict[str, Any]) -> str:
    tool = str(interrupt.get("tool_name") or "tool")
    args = json.dumps(interrupt.get("tool_args") or {}, sort_keys=True)
    reason = str(interrupt.get("reason") or interrupt.get("question") or "")
    return f"approval requested: {tool} {args} - {reason}".strip()


def _text(kind: str, summary: str, run_id: str, link: str) -> str:
    lines = [f"[comp-ops] {kind.replace('_', ' ')}"]
    if summary:
        lines.append(summary)
    lines.append(f"run: {run_id}")
    if link:
        lines.append(f"open: {link}")
    return "\n".join(lines)


# --- delivery ---------------------------------------------------------------


def deliver_webhook(config: dict[str, Any], text: str,
                    payload: dict[str, Any]) -> None:
    url = str(config.get("url") or "").strip()
    if not url:
        raise ValueError("webhook channel has no url")
    resp = httpx.post(url, json={**payload, "text": text},
                      timeout=SEND_TIMEOUT)
    resp.raise_for_status()


def deliver_email(config: dict[str, Any], subject: str, body: str) -> None:
    host = str(config.get("host") or "").strip()
    if not host:
        raise ValueError("email_smtp channel has no host")
    port = int(config.get("port") or 587)
    sender = str(config.get("from") or "comp-ops@localhost")
    recipient = str(config.get("to") or "").strip()
    if not recipient:
        raise ValueError("email_smtp channel has no to address")
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=SEND_TIMEOUT) as smtp:
        if config.get("starttls", True):
            smtp.starttls()
        user, password = config.get("user"), config.get("password")
        if user and password:
            smtp.login(str(user), str(password))
        smtp.sendmail(sender, [recipient], msg.as_string())


def deliver(channel: dict[str, Any], kind: str, summary: str,
            run_id: str, link: str) -> None:
    text = _text(kind, summary, run_id, link)
    ctype = str(channel.get("type") or "")
    if ctype == "webhook":
        deliver_webhook(channel.get("config") or {}, text, {
            "run_id": run_id, "kind": kind, "summary": summary, "link": link,
        })
    elif ctype == "email_smtp":
        subject = f"[comp-ops] {kind.replace('_', ' ')} - {run_id}"
        deliver_email(channel.get("config") or {}, subject, text)
    else:
        raise ValueError(f"unknown channel type {ctype!r}")


def notify(run_id: str, kind: str, summary: str, link: str = "") -> list[int]:
    """Queue one notification per enabled channel, then deliver each.

    The row is written BEFORE delivery (so the queue survives a crash) and
    updated to sent/failed afterwards. Never raises.
    """
    ids: list[int] = []
    for channel in db.list_channels():
        if not channel.get("enabled"):
            continue
        nid = db.record_notification(int(channel["id"]), run_id, kind,
                                     summary, link)
        ids.append(nid)
        try:
            deliver(channel, kind, summary, run_id, link)
        except Exception as exc:  # noqa: BLE001 - delivery must not kill a run
            db.finish_notification(nid, "failed",
                                   f"{type(exc).__name__}: {exc}")
            print(f"[channels] {kind} notification {nid} "
                  f"to {channel.get('name')!r} failed: {exc!r}")
        else:
            db.finish_notification(nid, "sent")
    return ids


def send_test(channel_id: int) -> tuple[str, str]:
    """Send a test message on one channel. Returns (status, error)."""
    channel = db.get_channel(int(channel_id))
    if channel is None:
        return "failed", "unknown channel"
    nid = db.record_notification(int(channel["id"]), "-", "test",
                                 "test message from the comp-ops dashboard")
    try:
        deliver(channel, "test",
                "test message from the comp-ops dashboard", "-", "")
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        db.finish_notification(nid, "failed", error)
        return "failed", error
    db.finish_notification(nid, "sent")
    return "sent", ""


# --- runner hooks -----------------------------------------------------------


def on_state(run_id: str, state: str,
             interrupt: dict[str, Any] | None = None) -> None:
    """Called by the runner at state transitions. Never raises.

    waiting_approval -> one approval notification (signed one-time link);
    failed/interrupted -> one alert. Cancelled runs are the user's own
    action and deliberately do not alert.
    """
    try:
        if state == "waiting_approval":
            if not any(c.get("enabled") for c in db.list_channels()):
                return  # no channels: do not mint links nobody will open
            payload = interrupt or {}
            link = f"{base_url()}/approve/{issue_link(run_id)}"
            notify(run_id, "waiting_approval",
                   approval_summary(payload), link)
        elif state in ("failed", "interrupted"):
            run = db.get_run(run_id) or {}
            task = str(run.get("task") or "")
            error = str(run.get("error") or "")
            summary = f"run {state}: {task}" if task else f"run {state}"
            if error:
                summary += f" ({error})"
            notify(run_id, state, summary, f"{base_url()}/runs/{run_id}")
    except Exception as exc:  # noqa: BLE001 - the run must never die here
        print(f"[channels] state notification for {run_id} "
              f"({state}) failed: {exc!r}")
