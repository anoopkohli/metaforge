"""Thin eval runner: run a function over cases, score each, report, compare to a baseline."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    id: str
    input: Any
    expected: Any = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    score: float
    passed: bool
    output: Any = None
    latency_s: float = 0.0
    error: str | None = None


@dataclass
class Report:
    name: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def mean_score(self) -> float:
        return sum(r.score for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def p95_latency_s(self) -> float:
        lat = sorted(r.latency_s for r in self.results)
        if not lat:
            return 0.0
        return lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n": len(self.results),
            "pass_rate": round(self.pass_rate, 4),
            "mean_score": round(self.mean_score, 4),
            "p95_latency_s": round(self.p95_latency_s, 6),
            "results": [asdict(r) for r in self.results],
        }


def load_jsonl(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line_no, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            raw = json.loads(line)
            cases.append(
                EvalCase(
                    id=raw["id"],
                    input=raw["input"],
                    expected=raw.get("expected"),
                    tags=tuple(raw.get("tags", ())),
                )
            )
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}:{line_no}: bad case ({exc})") from exc
    return cases


def run_suite(
    name: str,
    cases: list[EvalCase],
    fn: Callable[[EvalCase], Any],
    scorer: Callable[[Any, EvalCase], float],
    pass_threshold: float = 1.0,
) -> Report:
    """Run `fn` on each case. A raised exception scores 0 and is recorded, never swallowed."""
    report = Report(name=name)
    for case in cases:
        start = time.perf_counter()
        try:
            output = fn(case)
            latency = time.perf_counter() - start
            score = float(scorer(output, case))
            report.results.append(
                CaseResult(case.id, score, score >= pass_threshold, output, latency)
            )
        except Exception as exc:  # noqa: BLE001 - a failing case is a result, not a crash
            latency = time.perf_counter() - start
            report.results.append(
                CaseResult(case.id, 0.0, False, None, latency, f"{type(exc).__name__}: {exc}")
            )
    return report


def compare_to_baseline(report: Report, baseline: dict, max_drop: float = 0.02) -> list[str]:
    """Return regression messages (empty list means OK). CI fails when this is non-empty."""
    problems: list[str] = []
    base_rate = float(baseline.get("pass_rate", 0.0))
    if report.pass_rate < base_rate - max_drop:
        problems.append(
            f"{report.name}: pass_rate {report.pass_rate:.3f} < baseline {base_rate:.3f} "
            f"(allowed drop {max_drop})"
        )
    return problems
