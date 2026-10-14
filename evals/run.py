"""Run eval suites and gate them on committed baselines.

    python -m evals.run                                  # every offline suite, gated (what CI runs)
    python -m evals.run --suite rag                      # one suite
    python -m evals.run --update-baseline --reason "..." # record new baselines, with a reason
    python -m evals.run --live --max-cost-usd 1.00       # real model; reports only, never gates

Offline runs replay recorded or scripted model behaviour, so they are deterministic and their token
counts are gated too. Live runs need ANTHROPIC_API_KEY and the `llm` extra; the model under test is
METAFORGE_MODEL and the judge METAFORGE_JUDGE_MODEL, sharing one budget. Reports go to
evals/reports/ (gitignored); in GitHub Actions the summary table is added to the job summary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from evals.gate import (
    BaselineUpdateRefused,
    GateResult,
    check,
    check_all_suites_ran,
    summarize,
    update_baseline,
)
from evals.report import markdown, write_report
from evals.runner import run_suite
from evals.suites import BASELINES, SUITES, _smoke_build

HERE = Path(__file__).parent
REPORTS = HERE / "reports"

# Kept for callers and tests that predate the registry.
DATASET = HERE / "datasets" / "smoke.jsonl"
BASELINE = BASELINES / "smoke.json"


def _build():
    return _smoke_build("replay", None)


def _live_factories(budget_usd: float):
    from core.llm.anthropic_client import AnthropicLLM
    from evals.pricing import Ledger, MeteredLLM

    ledger = Ledger(budget_usd)
    model = os.environ.get("METAFORGE_MODEL", "claude-sonnet-5-5")
    judge_model = os.environ.get("METAFORGE_JUDGE_MODEL", "claude-opus-5-5")
    under_test = MeteredLLM(AnthropicLLM(model=model), model=model, ledger=ledger)
    judge = MeteredLLM(AnthropicLLM(model=judge_model), model=judge_model, ledger=ledger)
    return ledger, (lambda: under_test), (lambda: judge)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.run")
    parser.add_argument("--suite", choices=sorted(SUITES), action="append")
    parser.add_argument("--live", action="store_true", help="real model; never gates")
    parser.add_argument("--max-cost-usd", type=float, default=1.0)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--reason", default="", help="required with --update-baseline")
    parser.add_argument("--accept-regression", action="store_true")
    args = parser.parse_args(argv)
    if args.live and args.update_baseline:
        parser.error("baselines are offline only; --live cannot update them")
    if args.update_baseline and not args.reason.strip():
        parser.error("--update-baseline needs --reason")
    if args.live and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        parser.error(
            "--live needs ANTHROPIC_API_KEY in this shell's environment "
            "(export it in the same terminal that runs this command)"
        )

    mode = "live" if args.live else "replay"
    names = args.suite or [n for n, s in SUITES.items() if mode in s.modes]
    ledger = factory = judge_factory = None
    if args.live:
        ledger, factory, judge_factory = _live_factories(args.max_cost_usd)

    rows: list[tuple] = []
    failed = False
    ran: set[str] = set()
    for name in names:
        suite = SUITES[name]
        if mode not in suite.modes:
            print(f"{name}: skipped (no {mode} mode)")
            continue
        cases = suite.load_cases(mode)
        scorer = suite.scorer
        if args.live and suite.live_scorer is not None:
            scorer = suite.live_scorer(judge_factory)
        spent_before = ledger.spent_usd if ledger else 0.0
        build_factory = judge_factory if (args.live and suite.tests_the_judge) else factory
        report = run_suite(name, cases, suite.build(mode, build_factory), scorer)
        cost = (ledger.spent_usd - spent_before) if ledger else 0.0
        summary = summarize(report, cases, mode, cost_usd=cost)
        ran.add(name)
        write_report(REPORTS / f"{name}-{mode}.json", summary, report)
        print(
            f"{name}: n={summary.n} pass_rate={summary.pass_rate} mean_score={summary.mean_score} "
            f"p95_latency_s={summary.p95_latency_s} tokens_in={summary.tokens_in} "
            f"tokens_out={summary.tokens_out} cost_usd={summary.cost_usd}"
        )

        gate: GateResult | None = None
        if args.update_baseline:
            try:
                update_baseline(
                    suite.baseline,
                    summary,
                    reason=args.reason,
                    accept_regression=args.accept_regression,
                )
                print(f"  baseline written to {suite.baseline}")
            except BaselineUpdateRefused as exc:
                print(f"  REFUSED: {exc}")
                failed = True
        elif not args.live:
            if suite.baseline.exists():
                gate = check(
                    summary,
                    json.loads(suite.baseline.read_text()),
                    rate_tolerance=suite.rate_tolerance,
                    token_tolerance=suite.token_tolerance,
                )
            else:
                gate = GateResult(problems=[f"{name}: no baseline; run --update-baseline --reason"])
            for p in gate.problems:
                print("  REGRESSION:", p)
            for n in gate.notes:
                print("  note:", n)
            failed = failed or not gate.ok
        rows.append((summary, gate))

    if not args.live and not args.suite and not args.update_baseline:
        for problem in check_all_suites_ran(ran, BASELINES):
            print("REGRESSION:", problem)
            failed = True
    if ledger:
        print(
            f"live spend: ${ledger.spent_usd:.4f} of ${ledger.budget_usd:.2f} "
            f"({ledger.calls} calls)"
        )

    table = markdown(rows)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as fh:
            fh.write(f"## Evals ({mode})\n\n{table}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
