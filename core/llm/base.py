"""LLM adapter interface.

Invariant: all model calls in this repo go through `LLMClient`. Feature code never imports a
provider SDK. Tests use `FakeLLM`, so the suite runs offline and deterministically.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    latency_s: float = 0.0
    model: str = ""


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...


class FakeLLM:
    """Scripted client for tests and offline evals.

    `script` is either a list of response strings (consumed in order, the last one repeats)
    or a callable taking the message list and returning a string. Every call is recorded.
    """

    def __init__(self, script: Sequence[str] | Callable[[Sequence[Message]], str]) -> None:
        self._script = script
        self._i = 0
        self.calls: list[dict] = []

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        start = time.perf_counter()
        self.calls.append({"messages": list(messages), "system": system, "max_tokens": max_tokens})
        if callable(self._script):
            text = self._script(messages)
        else:
            if not self._script:
                raise RuntimeError("FakeLLM has an empty script")
            text = self._script[min(self._i, len(self._script) - 1)]
            self._i += 1
        # Rough, deterministic token estimate so cost tracking can be exercised offline.
        in_tok = sum(len(m.content.split()) for m in messages) + len((system or "").split())
        return LLMResponse(
            text=text,
            usage=Usage(input_tokens=in_tok, output_tokens=len(text.split())),
            latency_s=time.perf_counter() - start,
            model="fake",
        )
