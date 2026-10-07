"""Browser tools driven by a persistent Playwright worker thread.

Playwright's sync API is bound to the thread that started it, so one dedicated
worker owns the browser; every tool call is a queue request waited on with a
timeout. On timeout the caller gets a TimeoutError while the worker stays alive
for later calls.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai_operator.permissions import PermissionLevel
from ai_operator.tools.registry import tool

DEFAULT_TIMEOUT_MS = 8000
PAGE_TEXT_LIMIT = 6000


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
    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.evidence_dir: Path = Path("runs")
        self.timeout_ms = DEFAULT_TIMEOUT_MS

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="browser-worker", daemon=True)
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
                try:
                    result["value"] = self._dispatch(page, op, args)
                except Exception as exc:  # noqa: BLE001 - marshalled to caller
                    result["error"] = exc
                finally:
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
            try:
                page.click(target)
            except Exception as exc:  # noqa: BLE001
                page.locator(selector).first.evaluate("el => el.requestSubmit()")
            try:
                page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - navigation may already be done
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


WORKER = _BrowserWorker()


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


def shutdown() -> None:
    WORKER.stop()
