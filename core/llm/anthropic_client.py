"""Anthropic provider behind the LLMClient interface.

The SDK is imported lazily so the base install (and the offline test suite) never needs it.
Install with `pip install -e ".[llm]"` and set ANTHROPIC_API_KEY to use it.
Every request has a bounded timeout and retry count: one hung call must fail, not stall a run.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from core.llm.base import LLMResponse, Message, Usage


class AnthropicLLM:
    def __init__(
        self,
        model: str = "claude-sonnet-5-5",
        api_key: str | None = None,
        *,
        timeout_s: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError('anthropic SDK not installed: pip install -e ".[llm]"') from exc
        kwargs: dict = {"timeout": timeout_s, "max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = anthropic.Anthropic(**kwargs)
        self._model = model

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            kwargs["system"] = system
        start = time.perf_counter()
        resp = self._client.messages.create(**kwargs)
        latency = time.perf_counter() - start
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        return LLMResponse(
            text=text,
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", None) or 0,
                cache_creation_input_tokens=(
                    getattr(resp.usage, "cache_creation_input_tokens", None) or 0
                ),
            ),
            latency_s=latency,
            model=self._model,
        )
