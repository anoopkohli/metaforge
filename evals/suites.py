"""Registry of eval suites. One entry per suite; the CLI, CI gate and harness tests all read it.

Invariant: every suite declares a negative control (an output its scorer must reject), so a scorer
that passes everything is caught by `test_every_scorer_rejects_a_wrong_output`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.llm.base import LLMClient
from evals import generator_suite, rag_suite
from evals.runner import EvalCase, load_jsonl
from evals.scorers import fields_match

HERE = Path(__file__).parent
DATASETS = HERE / "datasets"
BASELINES = HERE / "baselines"

LLMFactory = Callable[[], LLMClient]
Scorer = Callable[[Any, EvalCase], float]


@dataclass(frozen=True)
class Suite:
    name: str
    modes: tuple[str, ...]
    load_cases: Callable[[str], list[EvalCase]]
    build: Callable[[str, LLMFactory | None], Callable[[EvalCase], Any]]
    scorer: Scorer
    negative_control: Callable[[EvalCase], Any]
    live_scorer: Callable[[LLMFactory], Scorer] | None = None
    rate_tolerance: float = 0.0
    token_tolerance: float = 0.05

    @property
    def baseline(self) -> Path:
        return BASELINES / f"{self.name}.json"


# ---- smoke ------------------------------------------------------------------------------------


def _smoke_build(mode: str, factory: LLMFactory | None) -> Callable[[EvalCase], Any]:
    from core.store.sqlite_store import MetadataStore
    from core.synthetic import seed_demo

    store = MetadataStore(":memory:")
    tenants = seed_demo(store, n_tenants=5, seed=7)

    def fn(case: EvalCase):
        tenant = tenants[case.input["tenant_index"]]
        schema = store.get_effective_schema(tenant.context(), case.input["object"])
        return [f.name for f in schema.fields] if schema else []

    return fn


# ---- generator --------------------------------------------------------------------------------


def _generator_negative(case: EvalCase) -> dict:
    if case.expected.get("action") is None:  # the case expects failure: claim success instead
        proposal = {"action": "create_object", "object": {"name": "x", "fields": []}}
        return {"ok": True, "proposal": proposal, "attempts": 1, "mode": "replay", "effective": []}
    return {"ok": False, "proposal": None, "attempts": 3, "mode": "replay", "effective": None}


# ---- mcp attacks ------------------------------------------------------------------------------


def _attack_cases(mode: str) -> list[EvalCase]:
    from attacks.mcp import ATTACKS

    return [EvalCase(id=a, input=a, tags=("attack",)) for a in sorted(ATTACKS)]


def _attack_build(mode: str, factory: LLMFactory | None) -> Callable[[EvalCase], Any]:
    from attacks.mcp import run_attack

    return lambda case: {"succeeded": run_attack(case.input)}


def _blocked(output: dict, case: EvalCase) -> float:
    return 0.0 if output["succeeded"] else 1.0


# ---- rag --------------------------------------------------------------------------------------


def _rag_negative(case: EvalCase) -> dict:
    return {
        "tenant_id": "fjord",
        "answer": "leaked",
        "citations": [],
        "retrieved_docs": [],
        "hit_tenants": ["cinder", "fjord"],
        "foreign_canary_in_answer": True,
        "fact_value": "x",
        "question": "q",
    }


# ---- judge calibration (live only) ------------------------------------------------------------


def _judge_build(mode: str, factory: LLMFactory | None) -> Callable[[EvalCase], Any]:
    from evals.judge import judge

    if factory is None:
        raise ValueError("judge_calibration needs a model (live mode)")
    llm = factory()

    def fn(case: EvalCase) -> dict:
        v = judge(llm, case.input["question"], case.input["reference"], case.input["candidate"])
        return {"verdict": v.verdict, "reason": v.reason}

    return fn


def _judge_score(output: dict, case: EvalCase) -> float:
    return 1.0 if output["verdict"] == case.expected else 0.0


def _judge_negative(case: EvalCase) -> dict:
    wrong = "incorrect" if case.expected != "incorrect" else "correct"
    return {"verdict": wrong, "reason": "negative control"}


def _jsonl(name: str) -> Callable[[str], list[EvalCase]]:
    return lambda mode: load_jsonl(DATASETS / f"{name}.jsonl")


SUITES: dict[str, Suite] = {
    s.name: s
    for s in (
        Suite(
            name="smoke",
            modes=("replay",),
            load_cases=_jsonl("smoke"),
            build=_smoke_build,
            scorer=fields_match,
            negative_control=lambda case: list(case.expected.get("absent", [])),
        ),
        Suite(
            name="generator",
            modes=("replay", "live"),
            load_cases=lambda mode: generator_suite.select_cases(
                load_jsonl(generator_suite.DATASET), mode
            ),
            build=generator_suite.build,
            scorer=generator_suite.proposal_matches,
            negative_control=_generator_negative,
        ),
        Suite(
            name="mcp_attacks",
            modes=("replay",),
            load_cases=_attack_cases,
            build=_attack_build,
            scorer=_blocked,
            negative_control=lambda case: {"succeeded": True},
        ),
        Suite(
            name="rag",
            modes=("replay", "live"),
            load_cases=_jsonl("rag"),
            build=rag_suite.build,
            scorer=rag_suite.rag_score,
            negative_control=_rag_negative,
            live_scorer=lambda judge_factory: rag_suite.live_scorer(judge_factory()),
        ),
        Suite(
            name="judge_calibration",
            modes=("live",),
            load_cases=_jsonl("judge_calibration"),
            build=_judge_build,
            scorer=_judge_score,
            negative_control=_judge_negative,
        ),
    )
}


def load_baseline(suite: Suite) -> dict | None:
    return json.loads(suite.baseline.read_text()) if suite.baseline.exists() else None
