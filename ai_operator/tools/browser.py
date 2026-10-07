"""Browser tools driven by a small pool of persistent Playwright worker threads.

Playwright's sync API is bound to the thread that started it, so each worker
owns its browser on its own thread; every tool call is a queue request waited
on with a timeout. On timeout the caller gets a TimeoutError while the worker
stays alive for later calls.

Routing is sticky per calling thread: a run's ops all land on the same worker
so page state stays coherent, while other threads use the second browser
instead of queueing behind a busy one. The pool exposes the same call
interface as a single worker, so no tool code changed.
"""

from __future__ import annotations

import queue
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.registry import tool

DEFAULT_TIMEOUT_MS = 8000
PAGE_TEXT_LIMIT = 6000
POOL_SIZE = 2


class NavigateArgs(BaseModel):
    url: str = Field(description="absolute http(s) URL to open")


class ReadPageArgs(BaseModel):
    pass


class ClickArgs(BaseModel):
    selector: str = Field(description="CSS selector of the element to click")


class FillArgs(BaseModel):
    selector: str = Field(description="CSS selector of the input")
    value: str = Field(description="value to type")


class SubmitFormArgs(BaseModel):
    selector: str = Field(default="form", description="form (or its submit button) CSS selector")


class ScreenshotArgs(BaseModel):
    tag: str = Field(default="screenshot", description="label for the evidence filename")


class _BrowserWorker:
    """One thread owning one Playwright browser; jobs arrive on its queue."""

    def __init__(self, index: int) -> None:
        self.index = index
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._processing = False
        self.evidence_dir: Path = Path("runs")
        self.timeout_ms = DEFAULT_TIMEOUT_MS

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def idle(self) -> bool:
        return self._q.empty() and not self._processing

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._loop, name=f"browser-worker-{self.index}", daemon=True
            )
            self._thread.start()

    def call(self, op: str, args: dict[str, Any], timeout: float) -> Any:
        self.start()
        done = threading.Event()
        result: dict[str, Any] = {}
        self._q.put((op, args, done, result))
        if not done.wait(timeout):
            raise TimeoutError(f"browser '{op}' did not finish within {timeout:.0f}s")
        if isinstance(result.get("error"), Exception):
            raise result["error"]
        return result.get("value")

    def stop(self) -> Any:
        if self._thread and self._thread.is_alive():
            return self.call("shutdown", {}, 10.0)
        return "browser not running"

    def _loop(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_default_timeout(self.timeout_ms)
            while True:
                op, args, done, result = self._q.get()
                if op == "shutdown":
                    browser.close()
                    result["value"] = "browser closed"
                    done.set()
                    return
                self._processing = True
                try:
                    result["value"] = self._dispatch(page, op, args)
                except Exception as exc:  # noqa: BLE001 - marshalled to caller
                    result["error"] = exc
                finally:
                    self._processing = False
                    done.set()

    @staticmethod
    def _dispatch(page: Any, op: str, args: dict[str, Any]) -> Any:
        if op == "navigate":
            page.goto(args["url"], wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return f"loaded {page.url} title={page.title()!r}"
        if op == "read_page":
            return _page_snapshot(page)
        if op == "click":
            page.click(args["selector"])
            return f"clicked {args['selector']}"
        if op == "fill":
            try:
                page.fill(args["selector"], args["value"])
            except Exception as exc:  # noqa: BLE001 - include real inputs for recovery
                inputs = _input_ids(page)
                raise RuntimeError(
                    f"fill failed for {args['selector']}: {exc}. "
                    f"Inputs present on page: {inputs}"
                ) from exc
            return f"filled {args['selector']} with {args['value']!r}"
        if op == "submit":
            selector = args.get("selector") or "form"
            target = selector
            if selector == "form":
                target = "form button[type='submit'], form input[type='submit']"
            # Fail fast with a useful message instead of blocking for the full timeout.
            button = page.locator(target).first
            try:
                button.wait_for(state="visible", timeout=4000)
            except Exception as exc:  # noqa: BLE001
                inputs = _input_ids(page)
                raise RuntimeError(
                    f"submit control {target!r} not found on {page.url}; "
                    f"inputs present: {inputs}; original error: {exc}"
                ) from exc
            url_before = page.url
            nav_error = None
            try:
                with page.expect_navigation(wait_until="load", timeout=4000):
                    button.click()
            except Exception as exc:  # noqa: BLE001 - may still have navigated
                nav_error = exc
            if page.url == url_before:
                detail = str(nav_error) if nav_error else "click completed without navigation"
                raise RuntimeError(
                    f"submit click failed: {detail}; page stayed at {page.url}"
                )
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001 - redirect chain already settled
                pass
            return f"submitted via {target}; now at {page.url}"
        if op == "screenshot":
            self_dir = Path(args["evidence_dir"])
            self_dir.mkdir(parents=True, exist_ok=True)
            path = self_dir / f"{args['tag']}.png"
            page.screenshot(path=str(path), full_page=True)
            return f"saved screenshot {path}"
        if op == "status":
            return {"url": page.url, "title": page.title()}
        if op == "close":
            page.close()
            return "page closed"
        raise ValueError(f"unknown browser op: {op}")


def _input_ids(page: Any) -> list[str]:
    return page.eval_on_selector_all(
        "input, textarea, select",
        "els => els.map(e => '#' + (e.id || e.name || e.type)).slice(0, 30)",
    )


def _page_snapshot(page: Any) -> str:
    inputs = page.eval_on_selector_all(
        "input, textarea, select",
        """els => els.map(e => ({
            sel: '#' + (e.id || e.name || e.type),
            type: e.type || e.tagName.toLowerCase(),
            name: e.name || '',
            placeholder: e.placeholder || '',
            value: e.value || ''
        }))""",
    )
    buttons = page.eval_on_selector_all(
        "button, input[type=submit]", "els => els.map(e => (e.innerText || e.value || '').trim())"
    )
    text = page.inner_text("body")[:PAGE_TEXT_LIMIT]
    lines = [f"url: {page.url}", f"title: {page.title()!r}", "inputs:"]
    lines += [f"  {i}" for i in inputs]
    lines.append(f"buttons: {buttons}")
    lines.append("visible text:")
    lines.append(text)
    return "\n".join(lines)


class _BrowserPool:
    """Sticky pool of browser workers with the single-worker call interface.

    Each calling thread is pinned to one worker, so a run's op sequence acts
    on one coherent page. Threads that have not spoken yet prefer an idle
    worker, so a second concurrent run gets the other browser instead of
    waiting behind a busy one.
    """

    def __init__(self, size: int = POOL_SIZE) -> None:
        self._workers = [_BrowserWorker(i) for i in range(size)]
        self._sticky: dict[int, int] = {}
        self._rr = 0
        self._lock = threading.Lock()
        self.evidence_dir: Path = Path("runs")
        self.timeout_ms = DEFAULT_TIMEOUT_MS

    @property
    def is_running(self) -> bool:
        return any(w.is_running for w in self._workers)

    def start(self) -> None:
        for w in self._workers:
            w.start()

    def _route(self) -> int:
        tid = threading.get_ident()
        idx = self._sticky.get(tid)
        if idx is not None:
            return idx
        with self._lock:
            # drop claims from threads that have exited (ids may be reused,
            # which is harmless: the new owner navigates first anyway)
            live = {t.ident for t in threading.enumerate()}
            self._sticky = {t: i for t, i in self._sticky.items() if t in live}
            idx = self._sticky.get(tid)
            if idx is not None:
                return idx
            claimed = set(self._sticky.values())
            free = [w.index for w in self._workers if w.index not in claimed]
            if free:
                idle_free = [i for i in free if self._workers[i].idle]
                idx = idle_free[0] if idle_free else free[0]
            else:
                # more live threads than workers: they must share a page
                idx = self._rr % len(self._workers)
                self._rr += 1
            self._sticky[tid] = idx
        return idx

    def call(self, op: str, args: dict[str, Any], timeout: float) -> Any:
        worker = self._workers[self._route()]
        worker.timeout_ms = self.timeout_ms
        worker.evidence_dir = self.evidence_dir
        return worker.call(op, args, timeout)

    def stop(self) -> Any:
        last: Any = "browser not running"
        for w in self._workers:
            if w.is_running:
                last = w.stop()
        return last


WORKER = _BrowserPool()


@tool("navigate", "Open a URL in the browser.", PermissionLevel.reversible_write, NavigateArgs)
def navigate(args: NavigateArgs) -> str:
    return str(WORKER.call("navigate", {"url": args.url}, _spec_timeout("navigate")))


@tool("read_page", "Read the current page: inputs with selectors, buttons, visible text.",
      PermissionLevel.read, ReadPageArgs)
def read_page(args: ReadPageArgs) -> str:
    return str(WORKER.call("read_page", {}, _spec_timeout("read_page")))


@tool("click", "Click an element by CSS selector.", PermissionLevel.reversible_write, ClickArgs)
def click(args: ClickArgs) -> str:
    return str(WORKER.call("click", {"selector": args.selector}, _spec_timeout("click")))


@tool("fill", "Fill an input field by CSS selector.", PermissionLevel.reversible_write, FillArgs)
def fill(args: FillArgs) -> str:
    return str(
        WORKER.call(
            "fill",
            {"selector": args.selector, "value": args.value},
            _spec_timeout("fill"),
        )
    )


@tool("submit_form", "Submit the form (creates a record; irreversible).",
      PermissionLevel.irreversible_write, SubmitFormArgs)
def submit_form(args: SubmitFormArgs) -> str:
    return str(WORKER.call("submit", {"selector": args.selector}, _spec_timeout("submit_form")))


@tool("screenshot", "Save a full-page screenshot as run evidence.",
      PermissionLevel.read, ScreenshotArgs)
def screenshot(args: ScreenshotArgs) -> str:
    return str(
        WORKER.call(
            "screenshot",
            {"tag": args.tag, "evidence_dir": str(WORKER.evidence_dir)},
            _spec_timeout("screenshot"),
        )
    )


def _spec_timeout(name: str) -> float:
    from ai_operator.tools.registry import get

    return get(name).timeout_seconds


def set_evidence_dir(path: Path) -> None:
    WORKER.evidence_dir = path


def capture_evidence(tag: str, timeout: float = 10.0) -> str | None:
    """Screenshot the current page for the run record; None if no browser is live."""
    if not WORKER.is_running:
        return None
    output = WORKER.call(
        "screenshot", {"tag": tag, "evidence_dir": str(WORKER.evidence_dir)}, timeout
    )
    m = re.search(r"saved screenshot (\S+)", str(output))
    return m.group(1) if m else None


def shutdown() -> Any:
    return WORKER.stop()
