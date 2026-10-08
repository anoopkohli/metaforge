"""Eval reports: JSON for machines, Markdown for people (and the GitHub job summary).

Invariant: anything written goes through `scrub`, which replaces the values of secret-looking
environment variables (KEY, TOKEN, SECRET, PASSWORD, DSN) with `[redacted]`. Reports go to the
gitignored `evals/reports/`; data in them is synthetic.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from evals.gate import GateResult, RunSummary
from evals.runner import Report

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|DSN|CREDENTIAL)", re.IGNORECASE)
_MIN_SECRET_LEN = 8
_MAX_ERROR_CHARS = 500


def scrub(text: str) -> str:
    for name, value in os.environ.items():
        if _SECRET_NAME.search(name) and value and len(value) >= _MIN_SECRET_LEN:
            text = text.replace(value, "[redacted]")
    return text


def write_report(path: Path, summary: RunSummary, report: Report) -> None:
    data = {
        "summary": summary.to_dict(),
        "results": [
            {
                "case_id": r.case_id,
                "passed": r.passed,
                "score": r.score,
                "latency_s": round(r.latency_s, 6),
                "error": (r.error or "")[:_MAX_ERROR_CHARS] or None,
                "output": r.output,
            }
            for r in report.results
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scrub(json.dumps(data, indent=2, default=str)) + "\n")


def markdown(rows: list[tuple[RunSummary, GateResult | None]]) -> str:
    lines = [
        "| Suite | Mode | Cases | Pass rate | Mean | p50 s | p95 s | Tokens in/out | Cost $ "
        "| Gate |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    details: list[str] = []
    for s, gate in rows:
        status = "n/a" if gate is None else ("pass" if gate.ok else "**FAIL**")
        lines.append(
            f"| {s.suite} | {s.mode} | {s.n} | {s.pass_rate:.4f} | {s.mean_score:.4f} | "
            f"{s.p50_latency_s:.4f} | {s.p95_latency_s:.4f} | {s.tokens_in}/{s.tokens_out} | "
            f"{s.cost_usd:.4f} | {status} |"
        )
        if gate:
            details += [f"- FAIL: {p}" for p in gate.problems]
            details += [f"- note: {n}" for n in gate.notes]
        failing = [cid for cid, ok in s.cases.items() if not ok]
        if failing:
            details.append(f"- {s.suite} failing cases: {', '.join(failing)}")
    return scrub("\n".join(lines + ([""] + details if details else [])) + "\n")
