"""Eval harness (the spine of the project).

`runner` runs cases through a function and scores them; `suites` registers every suite; `gate`
compares runs with committed baselines case by case; `pricing` meters live runs against a budget;
`judge` grades meaning in live runs; `report` writes scrubbed JSON and Markdown. CLI: `evals.run`.
"""

from evals.runner import CaseResult, EvalCase, Report, compare_to_baseline, load_jsonl, run_suite

__all__ = ["CaseResult", "EvalCase", "Report", "compare_to_baseline", "load_jsonl", "run_suite"]
