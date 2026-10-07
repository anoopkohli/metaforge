"""Eval harness (the spine of the project).

Stage 0 ships a thin runner: cases, scorers, a report with pass rate and latency, and a baseline
comparison that can fail CI. Later stages add datasets, LLM-as-judge scoring, and cost tracking.
"""

from evals.runner import CaseResult, EvalCase, Report, compare_to_baseline, load_jsonl, run_suite

__all__ = ["CaseResult", "EvalCase", "Report", "compare_to_baseline", "load_jsonl", "run_suite"]
