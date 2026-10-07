"""Run the stage-0 smoke eval: `python -m evals.run [--update-baseline]`.

The smoke suite checks the effective-schema logic over seeded synthetic tenants. It needs no LLM,
and it exists to prove the harness end to end (dataset -> run -> report -> baseline gate) before
any model-backed feature is added.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals.runner import EvalCase, compare_to_baseline, load_jsonl, run_suite
from evals.scorers import fields_match

HERE = Path(__file__).parent
DATASET = HERE / "datasets" / "smoke.jsonl"
BASELINE = HERE / "baselines" / "smoke.json"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)

    report = run_suite("smoke", load_jsonl(DATASET), _build(), fields_match)
    summary = report.to_dict()
    print(
        f"smoke: n={summary['n']} pass_rate={summary['pass_rate']} "
        f"p95_latency_s={summary['p95_latency_s']}"
    )

    if args.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"pass_rate": summary["pass_rate"]}, indent=2) + "\n")
        print(f"baseline written to {BASELINE}")
        return 0

    if BASELINE.exists():
        problems = compare_to_baseline(report, json.loads(BASELINE.read_text()))
        for p in problems:
            print("REGRESSION:", p)
        return 1 if problems else 0
    print("no baseline found; run with --update-baseline to create one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
