"""The live client must never wait indefinitely on one request (a hung call stalled a live eval run)."""

from __future__ import annotations

import sys
import types

from core.llm.anthropic_client import AnthropicLLM


def _fake_sdk(monkeypatch) -> dict:
    seen: dict = {}

    class Anthropic:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Anthropic))
    return seen


def test_requests_have_a_bounded_timeout_and_retries(monkeypatch):
    seen = _fake_sdk(monkeypatch)
    AnthropicLLM()
    assert 0 < seen["timeout"] <= 120
    assert 0 <= seen["max_retries"] <= 3


def test_timeout_is_configurable(monkeypatch):
    seen = _fake_sdk(monkeypatch)
    AnthropicLLM(api_key="test-not-a-key", timeout_s=5)
    assert seen["timeout"] == 5 and seen["api_key"] == "test-not-a-key"
