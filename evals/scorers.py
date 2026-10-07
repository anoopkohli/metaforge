"""Scorers: (output, case) -> score in [0, 1]."""

from __future__ import annotations

import json
from typing import Any

from evals.runner import EvalCase


def exact_match(output: Any, case: EvalCase) -> float:
    return 1.0 if output == case.expected else 0.0


def contains(output: Any, case: EvalCase) -> float:
    return 1.0 if str(case.expected) in str(output) else 0.0


def valid_json(output: Any, case: EvalCase) -> float:
    try:
        json.loads(output)
    except (TypeError, ValueError):
        return 0.0
    return 1.0


def fields_match(output: Any, case: EvalCase) -> float:
    """expected = {"present": [...], "absent": [...]}; score is the fraction satisfied."""
    names = set(output)
    present = case.expected.get("present", [])
    absent = case.expected.get("absent", [])
    checks = [n in names for n in present] + [n not in names for n in absent]
    return sum(checks) / len(checks) if checks else 1.0
