"""Run eval suites: `python -m evals.run [--suite NAME] [--live] [--update-baseline]`.

Offline (default) runs every suite and gates each on its committed baseline; this is what CI runs.
`--live` runs model-backed suites against a real provider (needs ANTHROPIC_API_KEY and the `llm`
extra). Live runs never gate: they write a report to `evals/reports/` for `docs/results-stage-N.md`.

The smoke suite checks the effective-schema logic over seeded synthetic tenants. It needs no LLM,
and it proves the harness end to end (dataset -> run -> report -> baseline gate).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from evals import generator_suite, rag_suite
from evals.runner import EvalCase, Report, compare_to_baseline, load_jsonl, run_suite
from evals.scorers import fields_match

HERE = Path(__file__).parent
DATASET = HERE / "datasets" / "smoke.jsonl"
BASELINE = HERE / "baselines" / "smoke.json"
REPORTS = HERE / "reports"


def _build():
    from core.store.sqlite_store import MetadataStore
    from core.synthetic import seed_demo

    store = MetadataStore(":memory:")
    tenants = seed_demo(store, n_tenants=5, seed=7)

    def fn(case: EvalCase):
        tenant = tenants[case.input["tenant_index"]]
        schema = store.get_effective_schema(tenant.context(), case.input["object"])
        return [f.name for f in schema.fields] if schema else []

    return fn


def _live_llm_factory():
    from core.llm.anthropic_client import AnthropicLLM

    model = os.environ.get("METAFORGE_MODEL", "claude-sonnet-5-5")
    return lambda: AnthropicLLM(model=model)


def _run(name: str, live: bool) -> tuple[Report, Path] | None:
    if name == "smoke":
        if live:
            return None  # no model involved
        return run_suite("smoke", load_jsonl(DATASET), _build(), fields_match), BASELINE
    if name == "generator":
        mode = "live" if live else "replay"
        cases = generator_suite.select_cases(load_jsonl(generator_suite.DATASET), mode)
        factory = _live_llm_factory() if live else None
        fn = generator_suite.build(mode, factory)
        return run_suite("generator", cases, fn, generator_suite.proposal_matches), (
            generator_suite.BASELINE
        )
    if name == "rag":
        mode = "live" if live else "replay"
        factory = _live_llm_factory() if live else None
        fn = rag_suite.build(mode, factory)
        cases = load_jsonl(rag_suite.DATASET)
        return run_suite("rag", cases, fn, rag_suite.rag_score), rag_suite.BASELINE
    if name == "mcp_attacks":
        if live:
            return None  # no model involved
        return run_suite("mcp_attacks", _attack_cases(), _run_attack, _blocked), MCP_BASELINE
    raise ValueError(f"unknown suite {name!r}")


# ---- stage 2: the MCP attack suite as an eval (score 1 = attack blocked) ----------------------

MCP_BASELINE = HERE / "baselines" / "mcp_attacks.json"


def _attack_cases() -> list[EvalCase]:
    from attacks.mcp import ATTACKS

    return [EvalCase(id=a, input=a, tags=("attack",)) for a in sorted(ATTACKS)]


def _run_attack(case: EvalCase) -> dict:
    from attacks.mcp import run_attack

    return {"succeeded": run_attack(case.input)}


def _blocked(output: dict, case: EvalCase) -> float:
    return 0.0 if output["succeeded"] else 1.0


SUITES = ("smoke", "generator", "mcp_attacks", "rag")


def _summary(report: Report) -> str:
    d = report.to_dict()
    line = (
        f"{report.name}: n={d['n']} pass_rate={d['pass_rate']} mean_score={d['mean_score']} "
        f"p95_latency_s={d['p95_latency_s']}"
    )
    usages = [
        r.output["usage"]
        for r in report.results
        if isinstance(r.output, dict) and "usage" in r.output
    ]
    if usages:
        tin = sum(u["input_tokens"] for u in usages)
        tout = sum(u["output_tokens"] for u in usages)
        line += f" tokens_in={tin} tokens_out={tout}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=SUITES, action="append")
    parser.add_argument("--live", action="store_true", help="use a real model; never gates")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    if args.live and args.update_baseline:
        parser.error("baselines are offline only; --live cannot update them")

    failed = False
    for name in args.suite or SUITES:
        ran = _run(name, args.live)
        if ran is None:
            continue
        report, baseline = ran
        print(_summary(report))
        for r in report.results:
            if not r.passed:
                print(f"  FAIL {r.case_id} score={r.score:.2f}{' ' + r.error if r.error else ''}")

        if args.live:
            REPORTS.mkdir(exist_ok=True)
            out = REPORTS / f"{name}-live.json"
            out.write_text(json.dumps(report.to_dict(), indent=2, default=str) + "\n")
            print(f"  report written to {out}")
        elif args.update_baseline:
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_text(
                json.dumps({"pass_rate": report.to_dict()["pass_rate"]}, indent=2) + "\n"
            )
            print(f"  baseline written to {baseline}")
        elif baseline.exists():
            for p in compare_to_baseline(report, json.loads(baseline.read_text())):
                print("REGRESSION:", p)
                failed = True
        else:
            print(f"  no baseline for {name}; run with --update-baseline to create one")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
