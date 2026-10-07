"""Stage-1 generator suite: description -> proposal, scored against a golden set.

Two modes over one dataset:
- `replay` (default, offline, CI): the model is a FakeLLM playing back recorded outputs, so the
  suite checks parsing, validation, repair and apply logic deterministically.
- `live`: a real model answers each description. Cases tagged `replay-only` are skipped, because
  they script specific model failures that a real model cannot be made to reproduce.

Every case runs against a fresh, seeded store, so cases cannot affect each other.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.llm.base import FakeLLM, LLMClient
from core.store.sqlite_store import MetadataStore
from core.synthetic import seed_demo
from evals.runner import EvalCase
from generator import CreateObject, Refuse, apply_proposal, generate

HERE = Path(__file__).parent
DATASET = HERE / "datasets" / "generator.jsonl"
BASELINE = HERE / "baselines" / "generator.json"


def select_cases(cases: list[EvalCase], mode: str) -> list[EvalCase]:
    return [c for c in cases if mode == "replay" or "replay-only" not in c.tags]


def build(
    mode: str = "replay", llm_factory: Callable[[], LLMClient] | None = None
) -> Callable[[EvalCase], dict]:
    if mode not in ("replay", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "live" and llm_factory is None:
        raise ValueError("live mode needs an llm_factory")

    def fn(case: EvalCase) -> dict:
        store = MetadataStore(":memory:")
        try:
            tenants = seed_demo(store, n_tenants=5, seed=7)
            ctx = tenants[case.input["tenant_index"]].context()
            if mode == "replay":
                script = [r if isinstance(r, str) else json.dumps(r) for r in case.input["replay"]]
                llm: LLMClient = FakeLLM(script)
            else:
                llm = llm_factory()  # type: ignore[misc]
            result = generate(llm, store, ctx, case.input["description"])

            effective = None
            if result.proposal is not None and not isinstance(result.proposal, Refuse):
                apply_proposal(store, ctx, result.proposal)
                p = result.proposal
                name = p.object.name if isinstance(p, CreateObject) else p.target
                schema = store.get_effective_schema(ctx, name)
                effective = [f.name for f in schema.fields] if schema else []
            return {
                "ok": result.ok,
                "proposal": result.proposal.model_dump(mode="json") if result.proposal else None,
                "attempts": len(result.attempts),
                "errors": [list(a.errors) for a in result.attempts],
                "mode": mode,
                "effective": effective,
                "usage": {
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                },
            }
        finally:
            store.close()

    return fn


def _pick(by_name: dict[str, dict], key: str) -> dict | None:
    """`key` may list alternatives as "a|b|c"; return the first field that exists."""
    for alt in key.split("|"):
        if alt in by_name:
            return by_name[alt]
    return None


def proposal_matches(output: dict[str, Any], case: EvalCase) -> float:
    """Fraction of the expected checks that hold. See the dataset header for the spec."""
    exp = case.expected
    p = output["proposal"]
    checks: list[bool] = []

    if "action" in exp:
        want = exp["action"]
        if want is None:
            checks.append(not output["ok"])
        else:
            wants = want if isinstance(want, list) else [want]
            checks.append(p is not None and p["action"] in wants)
    if "replay_attempts" in exp and output["mode"] == "replay":
        checks.append(output["attempts"] == exp["replay_attempts"])

    # Structural checks only apply to proposals that change metadata. A refusal that the case
    # allows is judged by the action check alone.
    if p is not None and p["action"] in ("create_object", "override_object"):
        if p["action"] == "create_object":
            name, fields = p["object"]["name"], p["object"]["fields"]
            hide, relabel = [], {}
        else:
            override = p["override"]
            name, fields = p["target"], override.get("add_fields", [])
            hide, relabel = override.get("hide_fields", []), override.get("relabel", {})
        by_name = {f["name"]: f for f in fields}
        effective = output.get("effective")

        if "name" in exp:
            checks.append(name in exp["name"].split("|"))
        for key, typ in exp.get("fields", {}).items():
            f = _pick(by_name, key)
            checks.append(f is not None and f["type"] == typ)
        for key in exp.get("required", []):
            f = _pick(by_name, key)
            checks.append(f is not None and f.get("required") is True)
        for key, target in exp.get("references", {}).items():
            f = _pick(by_name, key)
            checks.append(f is not None and f.get("reference_to") == target)
        checks += [h in hide for h in exp.get("hide", [])]
        checks += [r in relabel for r in exp.get("relabel", [])]
        checks += [n in (effective or []) for n in exp.get("effective_present", [])]
        checks += [
            effective is not None and n not in effective for n in exp.get("effective_absent", [])
        ]

    return sum(checks) / len(checks) if checks else 1.0
