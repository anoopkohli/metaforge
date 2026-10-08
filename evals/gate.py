"""Regression gate: summarise a run, compare it with a baseline, and update baselines on purpose.

Invariants:
- A run passes only if no case that passed in the baseline now fails, the pass rate has not dropped
  past the suite's tolerance, the dataset fingerprint matches, and (offline) token totals have not
  grown past the tolerance. The aggregate alone is never enough.
- A baseline changes only through `update_baseline`, which needs a reason, refuses to record a
  regression unless told to accept it, and appends every change to the baseline's history.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evals.runner import EvalCase, Report

BASELINE_VERSION = 2
HISTORY_LIMIT = 50


class BaselineUpdateRefused(RuntimeError):
    pass


def fingerprint(cases: list[EvalCase]) -> str:
    """Order-independent hash over every case's id, input, expected and tags."""
    rows = sorted(
        json.dumps([c.id, c.input, c.expected, sorted(c.tags)], sort_keys=True, default=str)
        for c in cases
    )
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]


@dataclass
class RunSummary:
    suite: str
    mode: str
    n: int
    pass_rate: float
    mean_score: float
    p50_latency_s: float
    p95_latency_s: float
    tokens_in: int
    tokens_out: int
    cost_usd: float
    errors: int
    fingerprint: str
    by_tag: dict[str, float] = field(default_factory=dict)
    cases: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def summarize(
    report: Report, cases: list[EvalCase], mode: str, cost_usd: float = 0.0
) -> RunSummary:
    tags = {c.id: c.tags for c in cases}
    tokens_in = tokens_out = 0
    tag_results: dict[str, list[bool]] = {}
    for r in report.results:
        usage = r.output.get("usage") if isinstance(r.output, dict) else None
        if usage:
            tokens_in += int(usage.get("input_tokens", 0))
            tokens_out += int(usage.get("output_tokens", 0))
        for tag in tags.get(r.case_id, ()):
            tag_results.setdefault(tag, []).append(r.passed)
    latencies = [r.latency_s for r in report.results]
    return RunSummary(
        suite=report.name,
        mode=mode,
        n=len(report.results),
        pass_rate=round(report.pass_rate, 4),
        mean_score=round(report.mean_score, 4),
        p50_latency_s=round(_percentile(latencies, 0.50), 6),
        p95_latency_s=round(_percentile(latencies, 0.95), 6),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=round(cost_usd, 6),
        errors=sum(1 for r in report.results if r.error),
        fingerprint=fingerprint(cases),
        by_tag={t: round(sum(v) / len(v), 4) for t, v in sorted(tag_results.items())},
        cases={r.case_id: r.passed for r in report.results},
    )


@dataclass
class GateResult:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check(
    summary: RunSummary,
    baseline: dict,
    *,
    rate_tolerance: float = 0.0,
    token_tolerance: float = 0.05,
) -> GateResult:
    result = GateResult()
    name = summary.suite
    if baseline.get("version") != BASELINE_VERSION:
        result.problems.append(f"{name}: baseline is not version {BASELINE_VERSION}; regenerate it")
        return result

    if summary.fingerprint != baseline["fingerprint"]:
        result.problems.append(
            f"{name}: dataset changed since the baseline (fingerprint {summary.fingerprint} != "
            f"{baseline['fingerprint']}); review the change, then --update-baseline --reason ..."
        )

    base_cases: dict[str, bool] = baseline["cases"]
    missing = sorted(set(base_cases) - set(summary.cases))
    if missing:
        result.problems.append(f"{name}: cases missing from this run: {missing}")
    added = sorted(set(summary.cases) - set(base_cases))
    if added:
        result.notes.append(f"{name}: new cases not in the baseline: {added}")

    for case_id, passed in sorted(base_cases.items()):
        now = summary.cases.get(case_id)
        if passed and now is False:
            result.problems.append(f"{name}: case {case_id!r} regressed (passed in baseline)")
        elif not passed and now:
            result.notes.append(
                f"{name}: case {case_id!r} now passes; update the baseline to lock it in"
            )

    if summary.pass_rate < baseline["pass_rate"] - rate_tolerance:
        result.problems.append(
            f"{name}: pass rate {summary.pass_rate:.4f} < baseline {baseline['pass_rate']:.4f}"
        )

    if summary.mode == "replay":
        base_in = baseline["tokens"]["input"]
        base_out = baseline["tokens"]["output"]
        for label, now, base in (
            ("input", summary.tokens_in, base_in),
            ("output", summary.tokens_out, base_out),
        ):
            if base and now > base * (1 + token_tolerance):
                result.problems.append(
                    f"{name}: {label} tokens {now} exceed baseline {base} by more than "
                    f"{token_tolerance:.0%} (prompt or context growth?)"
                )
            elif base and now < base * 0.8:
                result.notes.append(f"{name}: {label} tokens fell from {base} to {now}")
    return result


def check_all_suites_ran(ran: set[str], baselines_dir: Path) -> list[str]:
    """Every committed baseline must belong to a suite that ran. Catches suites dropped silently."""
    expected = {p.stem for p in Path(baselines_dir).glob("*.json")}
    missing = sorted(expected - ran)
    return [f"baselined suites did not run: {missing}"] if missing else []


def update_baseline(
    path: Path,
    summary: RunSummary,
    *,
    reason: str,
    accept_regression: bool = False,
    today: str | None = None,
) -> dict:
    if not reason or not reason.strip():
        raise ValueError("a baseline update needs a reason")
    path = Path(path)
    old = json.loads(path.read_text()) if path.exists() else None
    if old and old.get("version") == BASELINE_VERSION:
        lost = sorted(k for k, v in old["cases"].items() if v and not summary.cases.get(k))
        lowered = summary.pass_rate < old["pass_rate"]
        if (lost or lowered) and not accept_regression:
            raise BaselineUpdateRefused(
                f"{summary.suite}: this update records a regression (pass rate "
                f"{old['pass_rate']} -> {summary.pass_rate}, lost cases {lost}); "
                "pass --accept-regression if that is intended"
            )
        history = list(old.get("history", []))
    else:
        history = []
    history.append(
        {
            "date": today or dt.date.today().isoformat(),
            "reason": reason.strip(),
            "pass_rate": summary.pass_rate,
            "n": summary.n,
            "accepted_regression": accept_regression,
        }
    )
    data = {
        "version": BASELINE_VERSION,
        "suite": summary.suite,
        "fingerprint": summary.fingerprint,
        "n": summary.n,
        "pass_rate": summary.pass_rate,
        "mean_score": summary.mean_score,
        "tokens": {"input": summary.tokens_in, "output": summary.tokens_out},
        "cases": dict(sorted(summary.cases.items())),
        "history": history[-HISTORY_LIMIT:],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data
