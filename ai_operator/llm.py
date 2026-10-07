"""Model client: one small interface, three providers (openai, anthropic, stub).

Provider and model are chosen with environment variables. The API key lives in
.env and is never committed. The stub is a deterministic rule engine so tests
and the offline demo run without any network call to a model provider.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_TIMEOUT = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "60"))


class ModelClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Return the raw model text for one prompt pair."""
        ...


class OpenAICompatibleClient:
    def __init__(self, model: str, api_key: str, base_url: str, timeout: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions", headers=self.headers, json=payload
            )
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"])


class AnthropicClient:
    def __init__(self, model: str, api_key: str, timeout: float) -> None:
        self.model = model
        self.timeout = timeout
        self.headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                "https://api.anthropic.com/v1/messages", headers=self.headers, json=payload
            )
            resp.raise_for_status()
            return str(resp.json()["content"][0]["text"])


class StubClient:
    """Deterministic scripted model for tests and the offline demo.

    It reads the structured prompt sections ([HISTORY], [LAST OBSERVATION],
    [TASK]) and emits one decision. Clearly a stub — see KNOWN_LIMITATIONS.md.
    """

    def complete(self, system: str, user: str) -> str:
        if '"understanding":' in system:
            return json.dumps({
                "understanding": (
                    "Enter the newest Acme Corp invoice into the payables system and "
                    "confirm the record exists; policy may require approval."
                ),
                "plan": (
                    "1. Find Acme invoice files\n"
                    "2. Read them and pick the latest invoice date\n"
                    "3. Read policies.md and check the approval threshold\n"
                    "4. Open the add-invoice page and fill vendor, amount, due date\n"
                    "5. Submit the form\n"
                    "6. Run verification against the read-only API\n"
                    "7. Report summary with evidence"
                ),
            })
        decision = self._decide(f"{system}\n{user}")
        return json.dumps(decision)

    def _decide(self, prompt: str) -> dict:
        history = self._section(prompt, "HISTORY")
        observation = self._section(prompt, "LAST OBSERVATION")
        task = self._section(prompt, "TASK")
        # Result-aware parsing: only count completed ACTIONS, never output text.
        called = re.findall(r"^\s*\d+\.\s+(\w+)\(", history, flags=re.M)
        read_paths = re.findall(r"read_file\(path=['\"]?([\w./-]+)", history)
        filled = re.findall(r"fill\(selector=['\"]?([\w#-]+)['\"]?[^)]*\)\s*->\s*ok", history)

        if not called:
            return self._tool("search_files", {"query": "Acme Corp", "path": "company_data"},
                              "Start by finding the vendor's invoice files.")
        acme_all = [
            "company_data/invoices/acme_2026-05-10.txt",
            "company_data/invoices/acme_2026-06-15.txt",
            "company_data/invoices/acme_2026-07-20.txt",
        ]
        nxt = next((f for f in acme_all if f not in read_paths), None)
        if nxt:
            return self._tool("read_file", {"path": nxt},
                              "Read every Acme invoice to compare invoice dates.")
        if "company_data/policies.md" not in read_paths:
            return self._tool("read_file", {"path": "company_data/policies.md"},
                              "Check the approval policy before entering anything.")
        if "navigate" not in called:
            return self._tool("navigate", {"url": "http://127.0.0.1:8000/add"},
                              "Open the add-invoice page.")
        if "read_page" not in called:
            return self._tool("read_page", {}, "Inspect the page's actual inputs.")
        if "#vendor" not in filled:
            return self._tool("fill", {"selector": "#vendor", "value": "Acme Corp"},
                              "Fill the vendor field.")
        if "#amount" not in filled:
            return self._tool("fill", {"selector": "#amount", "value": "42500.00"},
                              "Fill the amount from the latest invoice.")
        if "#due_date" not in filled and "#due-date-field" not in filled:
            if "due-date-field" in observation:
                sel = "#due-date-field"
                thought = "The page renamed the due-date field; fill the selector that exists."
            else:
                sel = "#due_date"
                thought = "Fill the due date from the latest invoice."
            return self._tool("fill", {"selector": sel, "value": "2026-07-30"}, thought)
        if not any(c.startswith("submit") for c in called):
            return self._tool("submit_form", {"selector": "form"},
                              "Submit the form to create the record.")
        if "verify()" not in history:
            return {"thought": "Record submitted; ask deterministic code to verify it.",
                    "action_type": "verify", "tool_name": None, "tool_args": {},
                    "plan_update": None,
                    "expected_outcome": "payables system stores Acme Corp 42500.00 due 2026-07-30"}
        if "Acme" in task and not any(c == "screenshot" for c in called):
            return self._tool("screenshot", {"tag": "evidence"},
                              "Capture evidence before finishing.")
        return {"thought": "Verification matched the source invoice; report completion.",
                "action_type": "finish", "tool_name": None, "tool_args": {},
                "plan_update": None,
                "expected_outcome": "run ends verified_complete with evidence"}

    @staticmethod
    def _tool(name: str, args: dict, thought: str) -> dict:
        return {
            "thought": thought,
            "action_type": "tool_call",
            "tool_name": name,
            "tool_args": args,
            "plan_update": None,
            "expected_outcome": f"{name} succeeds and returns useful output",
        }

    @staticmethod
    def _section(prompt: str, name: str) -> str:
        m = re.search(rf"\[{re.escape(name)}\]\n(.*?)(?=\n\[|\Z)", prompt, flags=re.S)
        return m.group(1).strip() if m else ""


def get_client() -> ModelClient:
    provider = os.environ.get("MODEL_PROVIDER", "stub").strip().lower()
    model = os.environ.get("MODEL_NAME", "gpt-4o-mini")
    if provider == "stub":
        return StubClient()
    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError("MODEL_PROVIDER=openai requires OPENAI_API_KEY in .env")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return OpenAICompatibleClient(model, key, base, DEFAULT_TIMEOUT)
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError("MODEL_PROVIDER=anthropic requires ANTHROPIC_API_KEY in .env")
        return AnthropicClient(model, key, DEFAULT_TIMEOUT)
    raise RuntimeError(f"unknown MODEL_PROVIDER: {provider!r}")
