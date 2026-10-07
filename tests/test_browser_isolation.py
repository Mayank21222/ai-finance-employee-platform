"""Phase 3: per-agent browser isolation - node-id pinning in the pool."""

from __future__ import annotations

import json
import threading

from ai_operator.state import INITIAL_STATE_KEYS
from platform.flow.compiler import compile_flow
from platform.flow.models import Flow

CTX = {
    "tenant": "acme",
    "currency": "INR",
    "approval_threshold": 50000.0,
    "user_role": "op",
}

TWO_AGENTS = {
    "start_node_id": "a1",
    "session_context": CTX,
    "nodes": [
        {"type": "agent", "node_id": "a1", "save_as": "first",
         "next_node_ids": ["a2"], "fallback_next": "e"},
        {"type": "agent", "node_id": "a2", "save_as": "second",
         "next_node_ids": ["e"], "fallback_next": "e"},
        {"type": "end", "node_id": "e"},
    ],
}


class FinishImmediatelyClient:
    def complete(self, system: str, user: str) -> str:
        if '"understanding":' in system:
            return json.dumps({"understanding": "u", "plan": "p"})
        return json.dumps({
            "thought": "answering immediately",
            "action_type": "finish", "tool_name": None, "tool_args": {},
            "plan_update": None, "expected_outcome": "DONE",
        })


class UnderstandFailsClient:
    def complete(self, system: str, user: str) -> str:
        return "not json at all"


def _run(flow: Flow, client) -> dict:
    graph = compile_flow(flow, client).compile()
    return graph.invoke({
        "task": "browser isolation unit task",
        "run_id": "test_browser_isolation",
        **INITIAL_STATE_KEYS,
    })


# --- pool mechanics (no Playwright started: claims only) -------------------


def test_pool_pins_agent_across_threads_and_isolates_next_agent():
    from ai_operator.tools.browser import _BrowserPool

    pool = _BrowserPool(size=2)
    idx_a = pool.pin_agent("agent_a")
    assert pool.active_agent == "agent_a"
    assert pool._route() == idx_a

    # Node-id stickiness: a different internal thread still hits the pin.
    got: dict[str, int] = {}
    t = threading.Thread(target=lambda: got.update(idx=pool._route()))
    t.start()
    t.join()
    assert got["idx"] == idx_a

    # A second agent gets a different worker (auto-release of the first).
    idx_b = pool.pin_agent("agent_b")
    assert idx_b != idx_a
    assert pool.active_agent == "agent_b"
    assert pool._route() == idx_b

    # Release gives the worker back, but evidence routing keeps last page.
    pool.release_agent("agent_b")
    assert pool.active_agent is None
    assert pool._agent_pins == {}
    assert pool._route() == idx_b

    # The next agent prefers a fresh page over the last-used worker.
    idx_c = pool.pin_agent("agent_c")
    assert idx_c != idx_b

    pool.stop()
    assert pool.active_agent is None
    assert pool._agent_pins == {}
    assert pool._default_worker is None


def test_release_of_unpinned_agent_is_a_noop():
    from ai_operator.tools.browser import _BrowserPool

    pool = _BrowserPool(size=2)
    pool.release_agent("never_pinned")
    assert pool.active_agent is None
    idx_a = pool.pin_agent("agent_a")
    pool.release_agent("someone_else")
    assert pool.active_agent == "agent_a"  # only the owner is cleared
    assert pool._route() == idx_a


# --- compiler wiring --------------------------------------------------------


def test_two_agents_pin_and_release_in_order(monkeypatch):
    from ai_operator.tools import browser as browser_mod

    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        browser_mod, "pin_agent",
        lambda a: (events.append(("pin", a)), 0)[1],
    )
    monkeypatch.setattr(
        browser_mod, "release_agent",
        lambda a: events.append(("release", a)),
    )
    _run(Flow.model_validate(TWO_AGENTS), FinishImmediatelyClient())
    # entry pins (traced), decide re-pins idempotently, router releases.
    assert events == [("pin", "a1"), ("pin", "a1"), ("release", "a1"),
                      ("pin", "a2"), ("pin", "a2"), ("release", "a2")]


def test_failing_understand_still_releases_the_pin(monkeypatch):
    from ai_operator.tools import browser as browser_mod

    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        browser_mod, "pin_agent",
        lambda a: (events.append(("pin", a)), 0)[1],
    )
    monkeypatch.setattr(
        browser_mod, "release_agent",
        lambda a: events.append(("release", a)),
    )
    flow = Flow.model_validate({
        "start_node_id": "a1",
        "session_context": CTX,
        "nodes": [
            {"type": "agent", "node_id": "a1", "next_node_ids": ["e"],
             "fallback_next": "e"},
            {"type": "end", "node_id": "e"},
        ],
    })
    _run(flow, UnderstandFailsClient())
    assert events == [("pin", "a1"), ("release", "a1")]


def test_real_pool_state_is_clean_after_a_full_run():
    """The shipped flow run through a real (browserless) execution."""
    from ai_operator.graph import load_default_flow
    from ai_operator.tools.browser import WORKER

    class ClassifyThenFinish:
        """Finish at every agent without calling any browser tool."""

        def complete(self, system: str, user: str) -> str:
            if '"understanding":' in system:
                return json.dumps({"understanding": "u", "plan": "p"})
            if "Available specialists" in user:
                return json.dumps({
                    "thought": "reminder task", "action_type": "finish",
                    "tool_name": None, "tool_args": {}, "plan_update": None,
                    "expected_outcome": "payment_reminder",
                })
            return json.dumps({
                "thought": "done", "action_type": "finish",
                "tool_name": None, "tool_args": {}, "plan_update": None,
                "expected_outcome": "INV-X - Vendor - due (overdue)",
            })

    graph = compile_flow(load_default_flow(), ClassifyThenFinish()).compile()
    graph.invoke({
        "task": "remind all vendors with overdue invoices",
        "run_id": "test_browser_isolation",
        **INITIAL_STATE_KEYS,
    })
    # Every agent released its pin; no Playwright worker was ever started.
    assert WORKER.active_agent is None
    assert WORKER._agent_pins == {}
    assert not WORKER.is_running
