"""Run one attack suite in secure and naive mode and print JSON: `python -m attacks.cli <suite>`.

Used by the web UI's security lab as a child process. Naive mode needs exact environment values;
this process sets them for itself only, so the web server that launched it never has them.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import contextmanager

NAIVE = {
    "METAFORGE_UNSAFE_NAIVE_MCP": "trust-model-tenant-arg",
    "METAFORGE_UNSAFE_NAIVE_RAG": "disable-tenant-filters",
    "METAFORGE_UNSAFE_NAIVE_TRIAGE": "trust-the-agent",
    "METAFORGE_UNSAFE_NAIVE_DATAQA": "skip-access-checks",
}


@contextmanager
def _naive(on: bool):
    saved = {k: os.environ.get(k) for k in NAIVE}
    for k, v in NAIVE.items():
        if on:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run(suite: str) -> dict:
    results: list[dict] = []
    pooled = False
    if suite == "mcp":
        from attacks.mcp import ATTACKS, NAIVE_EXPECTED_TO_SUCCEED, run_attack

        for aid in sorted(ATTACKS):
            with _naive(False):
                secure = run_attack(aid)
            with _naive(True):
                naive = run_attack(aid, naive=True)
            results.append(
                {
                    "id": aid,
                    "secure": secure,
                    "naive": naive,
                    "naive_expected": aid in NAIVE_EXPECTED_TO_SUCCEED,
                }
            )
    elif suite == "triage":
        from attacks.triage import ATTACKS, run_triage_attack

        for aid in ATTACKS:
            with _naive(False):
                secure = run_triage_attack(aid)
            with _naive(True):
                naive = run_triage_attack(aid, naive=True)
            results.append({"id": aid, "secure": secure, "naive": naive, "naive_expected": True})
    elif suite == "dataqa":
        from attacks.dataqa import ATTACKS, NAIVE_EXPECTED_TO_SUCCEED, run_dataqa_attack

        for aid in ATTACKS:
            with _naive(False):
                secure = run_dataqa_attack(aid)
            with _naive(True):
                naive = run_dataqa_attack(aid, naive=True)
            results.append(
                {
                    "id": aid,
                    "secure": secure,
                    "naive": naive,
                    "naive_expected": aid in NAIVE_EXPECTED_TO_SUCCEED,
                }
            )
    elif suite == "rag":
        from attacks.rag import ATTACKS, applies, expected_success, run_rag_attack
        from rag.pg_index import discover_dsns

        pooled = discover_dsns() is not None
        for aid in ATTACKS:
            row: dict = {"id": aid}
            configs = [("silo", False, True), ("silo", True, True)]
            if pooled:
                configs += [
                    ("pooled", False, True),
                    ("pooled", True, True),
                    ("pooled", True, False),
                ]
            for strategy, naive, rls in configs:
                if not applies(aid, strategy):
                    continue
                with _naive(naive):
                    leaked = run_rag_attack(aid, strategy, naive=naive, rls=rls)
                key = f"{strategy}_{'naive' if naive else 'secure'}{'' if rls else '_norls'}"
                row[key] = leaked
                row[f"{key}_expected"] = expected_success(aid, strategy, naive, rls)
            row["secure"] = any(v for k, v in row.items() if "_secure" in k and "expected" not in k)
            row["naive"] = any(v for k, v in row.items() if "_naive" in k and "expected" not in k)
            row["naive_expected"] = any(
                v for k, v in row.items() if "_naive" in k and k.endswith("_expected")
            )
            results.append(row)
    else:
        raise SystemExit(f"unknown suite {suite!r}; expected mcp, rag, triage or dataqa")
    return {"suite": suite, "pooled": pooled, "results": results}


def main(argv: list[str] | None = None) -> int:
    logging.disable(logging.CRITICAL)  # keep stdout pure JSON
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m attacks.cli mcp|rag|triage|dataqa", file=sys.stderr)
        return 2
    print(json.dumps(run(args[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
