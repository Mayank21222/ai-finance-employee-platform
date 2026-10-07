"""Phase 3: model client selection (LLM_* env aliases, STUB_MODEL override)."""

import pytest

from ai_operator.llm import AnthropicClient, StubClient, get_client


def test_default_is_stub(monkeypatch):
    for var in ("STUB_MODEL", "LLM_PROVIDER", "MODEL_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(get_client(), StubClient)


def test_stub_model_env_forces_stub(monkeypatch):
    monkeypatch.setenv("STUB_MODEL", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert isinstance(get_client(), StubClient)


def test_llm_provider_wins_and_builds_anthropic_client(monkeypatch):
    monkeypatch.delenv("STUB_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("MODEL_PROVIDER", "stub")  # old name must lose
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    client = get_client()
    assert isinstance(client, AnthropicClient)
    assert client.model == "claude-sonnet-4-6"  # Phase-3 default


def test_llm_model_alias_sets_model_name(monkeypatch):
    monkeypatch.delenv("STUB_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-4-6")
    assert get_client().model == "claude-sonnet-4-6"


def test_real_provider_without_key_fails_loudly(monkeypatch):
    monkeypatch.delenv("STUB_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        get_client()
