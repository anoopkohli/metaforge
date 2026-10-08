"""Model pricing and a budget-capped LLM wrapper for live eval runs.

Invariants:
- Cost is computed only from this table. A model that is not in it is refused, never priced at zero
  (except the offline `fake` model).
- `MeteredLLM` refuses to make a call once the run has spent its budget. A single call can overshoot
  (its size is unknown until it returns), so the cap bounds spend to budget + one call.

Prices: Anthropic first-party API, USD per million tokens, as of 2026-10-06. Cache writes (5-minute
TTL) bill at 1.25x the input price.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.llm.base import LLMClient, LLMResponse, Message, Usage

CACHE_WRITE_MULTIPLIER = 1.25


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-fable-5-1": Price(10.00, 50.00, 0.25),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.20),
    # Haiku 5.5 prices apply to prompts up to 100K tokens; eval prompts stay far below that. Its
    # cache-read price was not in the source table, so cache reads are charged at the full input
    # price: an upper bound, which is the safe direction for a budget cap.
    "claude-haiku-5-5": Price(0.10, 0.50, 0.10),
}


class UnknownModel(ValueError):
    pass


class BudgetExceeded(RuntimeError):
    pass


def price_for(model: str) -> Price | None:
    if model == "fake":
        return None
    if model not in PRICES:
        raise UnknownModel(f"no price for {model!r}; add it to evals/pricing.py before a live run")
    return PRICES[model]


def cost_usd(model: str, usage: Usage) -> float:
    price = price_for(model)
    if price is None:
        return 0.0
    per_token = 1 / 1_000_000
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_input_tokens * price.cache_read
        + usage.cache_creation_input_tokens * price.input * CACHE_WRITE_MULTIPLIER
    ) * per_token


@dataclass
class Ledger:
    """One spend cap shared by every metered client in a run (e.g. the model under test and the
    judge), so the budget covers the whole run, not each client separately."""

    budget_usd: float
    spent_usd: float = 0.0
    calls: int = 0

    def __post_init__(self) -> None:
        if self.budget_usd <= 0:
            raise ValueError("budget_usd must be positive")


class MeteredLLM:
    """Wraps an `LLMClient`, totals usage and cost, and enforces a spend cap."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        model: str,
        budget_usd: float | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        price_for(model)  # refuse unknown models up front
        if ledger is None:
            if budget_usd is None:
                raise ValueError("give a budget_usd or a shared ledger")
            ledger = Ledger(budget_usd)
        self.inner = inner
        self.model = model
        self.ledger = ledger
        self.usage = Usage()

    @property
    def spent_usd(self) -> float:
        return self.ledger.spent_usd

    def complete(
        self, messages: Sequence[Message], *, system: str | None = None, max_tokens: int = 1024
    ) -> LLMResponse:
        ledger = self.ledger
        if ledger.spent_usd >= ledger.budget_usd:
            raise BudgetExceeded(
                f"live eval budget of ${ledger.budget_usd:.2f} reached "
                f"(spent ${ledger.spent_usd:.4f} over {ledger.calls} calls)"
            )
        resp = self.inner.complete(messages, system=system, max_tokens=max_tokens)
        ledger.calls += 1
        u = resp.usage
        self.usage = Usage(
            self.usage.input_tokens + u.input_tokens,
            self.usage.output_tokens + u.output_tokens,
            self.usage.cache_read_input_tokens + u.cache_read_input_tokens,
            self.usage.cache_creation_input_tokens + u.cache_creation_input_tokens,
        )
        ledger.spent_usd += cost_usd(self.model, u)
        return resp
