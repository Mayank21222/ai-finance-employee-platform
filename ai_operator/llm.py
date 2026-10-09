"""One clean model interface. Stub by default; OpenAI-compatible local or
cloud providers are optional and never a prerequisite."""

from __future__ import annotations

import json
import os
import re
import time

import httpx
from pydantic import ValidationError

from ai_operator.models import Decision
from ai_operator.observability import log_event


def _first_json_object(text: str) -> str | None:
    """Return the first balanced {...} block, ignoring braces inside strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_decision(text: str) -> Decision:
    """Extract and validate the JSON decision from arbitrary model text."""
    block = _first_json_object(text)
    if block is not None:
        try:
            return Decision(**json.loads(block))
        except (json.JSONDecodeError, ValidationError):
            pass
    # fall back to a permissive greedy match (e.g. fenced or wrapped output)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValidationError.from_exception_data("Decision", [{"loc": ("json",), "msg": "no JSON object found", "type": "value_error"}])
    return Decision(**json.loads(match.group(0)))


class ModelClient:
    """Contract: every provider returns a validated Decision for the prompt."""

    name = "base"

    def decide(self, prompt: str) -> Decision:
        raise NotImplementedError


class StubModel(ModelClient):
    """Deterministic demo/test model. Plays back a script of decisions."""

    name = "stub"

    def __init__(self, script: list[Decision | dict] | None = None):
        self.script = [d if isinstance(d, Decision) else Decision(**d) for d in (script or [])]
        self._i = 0

    def decide(self, prompt: str) -> Decision:
        if self._i < len(self.script):
            d = self.script[self._i]
            self._i += 1
            log_event("model_call", provider="stub", step=self._i, reply=d.model_dump())
            return d
        d = Decision(thought="Starting verification.", action_type="verify")
        log_event("model_call", provider="stub", step=self._i, reply=d.model_dump())
        return d


class OpenAICompatibleModel(ModelClient):
    """OpenAI chat-completions format: Ollama, vLLM, groq, openai, etc."""

    def __init__(self, name: str, base_url: str, api_key: str = "", provider: str = "local"):
        self.name = name
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def decide(self, prompt: str) -> Decision:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.name, "temperature": 0, "max_tokens": 700,
                "messages": [{"role": "user", "content": prompt}]}
        if self.provider == "groq":
            body["max_tokens"] = 2048
            body["reasoning_effort"] = "low"
        log_event("model_request", provider=self.provider, model=self.name, prompt=prompt)
        # Transient provider failures (429 rate limits, 5xx, network drops) are
        # retried with backoff — a single 429 used to kill the whole run.
        text = ""
        for attempt in range(4):
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=90)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"].get("content") or ""
                break
            except Exception as exc:  # noqa: BLE001
                transient = isinstance(exc, httpx.TransportError) or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in (408, 409, 429, 500, 502, 503, 504))
                log_event("model_error", level="error", provider=self.provider, model=self.name,
                          error=f"{type(exc).__name__}: {exc}", attempt=attempt + 1,
                          retrying=bool(transient and attempt < 3))
                if not transient or attempt >= 3:
                    raise
                time.sleep((2, 5, 12)[attempt])  # 2s, 5s, 12s — ride out rate windows
                log_event("model_retry", provider=self.provider, model=self.name,
                          attempt=attempt + 1)
        if not text.strip():
            log_event("model_error", level="error", provider=self.provider, model=self.name,
                      error="empty content (reasoning likely consumed all tokens)")
            raise ValueError("model returned no content")
        d = parse_decision(text)
        log_event("model_call", provider=self.provider, model=self.name, reply=text, decision=d.model_dump())
        return d


class AnthropicModel(ModelClient):
    def __init__(self, name: str, api_key: str):
        self.name, self.api_key = name, api_key

    def decide(self, prompt: str) -> Decision:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.name, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return parse_decision(r.json()["content"][0]["text"])


class GeminiModel(ModelClient):
    def __init__(self, name: str, api_key: str):
        self.name, self.api_key = name, api_key

    def decide(self, prompt: str) -> Decision:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.name}:generateContent",
            params={"key": self.api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        r.raise_for_status()
        return parse_decision(r.json()["candidates"][0]["content"]["parts"][0]["text"])


def get_model(provider: str | None = None, name: str | None = None, base_url: str | None = None) -> ModelClient:
    """Factory. Passed values win over env (MODEL_PROVIDER/MODEL_NAME/MODEL_BASE_URL).

    Cloud providers without a configured key degrade to the offline stub
    instead of crashing — the platform must run with no paid API key at all.
    """
    provider = (provider or os.getenv("MODEL_PROVIDER", "stub")).lower()
    default_name = {"groq": "openai/gpt-oss-120b",
                    "local": "llama3.1:8b", "ollama": "llama3.1:8b", "vllm": "llama3.1:8b"}.get(provider, "local-model")
    name = name or os.getenv("MODEL_NAME") or default_name
    base_url = base_url or os.getenv("MODEL_BASE_URL", "http://localhost:11434/v1")

    if provider == "stub":
        return StubModel()
    if provider in ("local", "ollama", "vllm", "openai", "groq"):
        key = {"openai": os.getenv("OPENAI_API_KEY", ""), "groq": os.getenv("GROQ_API_KEY", "")}.get(provider, "")
        if provider == "openai":
            base_url = "https://api.openai.com/v1"
        elif provider == "groq":
            base_url = "https://api.groq.com/openai/v1"
            if not key:
                # No key configured — fall back to the offline stub rather than
                # crashing the whole platform on first run.
                log_event("model_fallback", level="warning", provider=provider,
                          reason="no API key configured; using offline stub",
                          requested=name)
                return StubModel()
        elif provider == "ollama":
            base_url = base_url or "http://localhost:11434/v1"
        return OpenAICompatibleModel(name, base_url, key, provider=provider)
    if provider == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY", ""):
            log_event("model_fallback", level="warning", provider=provider,
                      reason="no ANTHROPIC_API_KEY configured; using offline stub")
            return StubModel()
        return AnthropicModel(name, os.getenv("ANTHROPIC_API_KEY", ""))
    if provider == "gemini":
        if not os.getenv("GEMINI_API_KEY", ""):
            log_event("model_fallback", level="warning", provider=provider,
                      reason="no GEMINI_API_KEY configured; using offline stub")
            return StubModel()
        return GeminiModel(name, os.getenv("GEMINI_API_KEY", ""))
    raise ValueError(f"unknown MODEL_PROVIDER '{provider}' (stub|local|openai|anthropic|gemini|groq)")


# Default canned script used by the CLI demo in stub mode.
DEFAULT_STUB_SCRIPT: list[dict] = [
    {"thought": "Find Acme invoices in company data.", "action_type": "tool_call", "tool_name": "search_files", "tool_args": {"query": "Acme"}},
    {"thought": "Read the latest invoice file.", "action_type": "tool_call", "tool_name": "read_file", "tool_args": {"path": ""}},
    {"thought": "Check the payment policy.", "action_type": "tool_call", "tool_name": "read_file", "tool_args": {"path": "company_data/policies.md"}},
    {"thought": "Open the invoice form.", "action_type": "tool_call", "tool_name": "browser_navigate", "tool_args": {"url": "/invoice/new"}},
    {"thought": "Fill in the invoice details.", "action_type": "tool_call", "tool_name": "browser_fill", "tool_args": {"field": "vendor", "value": "Acme Corp"}},
    {"thought": "Fill in the invoice amount.", "action_type": "tool_call", "tool_name": "browser_fill", "tool_args": {"field": "amount", "value": "42500"}},
    {"thought": "Fill in the due date.", "action_type": "tool_call", "tool_name": "browser_fill", "tool_args": {"field": "due_date", "value": "2026-07-30"}},
    {"thought": "Submit the invoice form.", "action_type": "tool_call", "tool_name": "browser_submit", "tool_args": {}},
    {"thought": "Confirm the record exists in the system.", "action_type": "verify"},
    {"thought": "Done.", "action_type": "finish"},
]