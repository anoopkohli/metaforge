import json

import pytest

from core.llm import FakeLLM, LLMClient, Message
from core.store import MetadataStore
from core.synthetic import seed_demo
from evals import EvalCase, compare_to_baseline, load_jsonl, run_suite
from evals.run import BASELINE, DATASET, _build, main
from evals.scorers import contains, exact_match, fields_match

# ---- synthetic data ---------------------------------------------------------------------------


def test_seed_is_deterministic():
    a = seed_demo(MetadataStore(":memory:"), n_tenants=5, seed=42)
    b = seed_demo(MetadataStore(":memory:"), n_tenants=5, seed=42)
    assert [(t.tenant_id, t.canary) for t in a] == [(t.tenant_id, t.canary) for t in b]


def test_different_seeds_differ():
    a = seed_demo(MetadataStore(":memory:"), n_tenants=5, seed=1)
    b = seed_demo(MetadataStore(":memory:"), n_tenants=5, seed=2)
    assert [t.canary for t in a] != [t.canary for t in b]


def test_tenant_count_bounds():
    with pytest.raises(ValueError):
        seed_demo(MetadataStore(":memory:"), n_tenants=0)
    with pytest.raises(ValueError):
        seed_demo(MetadataStore(":memory:"), n_tenants=99)


# ---- LLM adapter ------------------------------------------------------------------------------


def test_fake_llm_satisfies_the_protocol_and_records_calls():
    llm = FakeLLM(["one", "two"])
    assert isinstance(llm, LLMClient)
    r1 = llm.complete([Message("user", "hello there")], system="be brief")
    r2 = llm.complete([Message("user", "again")])
    r3 = llm.complete([Message("user", "and again")])
    assert (r1.text, r2.text, r3.text) == ("one", "two", "two")  # last response repeats
    assert r1.usage.input_tokens == 4 and r1.usage.output_tokens == 1
    assert len(llm.calls) == 3 and llm.calls[0]["system"] == "be brief"


def test_fake_llm_callable_script():
    llm = FakeLLM(lambda msgs: msgs[-1].content.upper())
    assert llm.complete([Message("user", "shout")]).text == "SHOUT"


def test_fake_llm_empty_script_fails_loudly():
    with pytest.raises(RuntimeError):
        FakeLLM([]).complete([Message("user", "x")])


# ---- eval harness -----------------------------------------------------------------------------


def test_run_suite_scores_and_reports():
    cases = [EvalCase("a", 1, 2), EvalCase("b", 2, 4), EvalCase("c", 3, 7)]
    report = run_suite("double", cases, lambda c: c.input * 2, exact_match)
    assert [r.passed for r in report.results] == [True, True, False]
    assert report.pass_rate == pytest.approx(2 / 3)
    assert report.to_dict()["n"] == 3


def test_run_suite_records_exceptions_instead_of_crashing():
    def boom(case):
        raise ValueError("nope")

    report = run_suite("boom", [EvalCase("a", 1, 1)], boom, exact_match)
    assert report.results[0].passed is False
    assert report.results[0].error == "ValueError: nope"


def test_contains_scorer():
    assert contains("the CANARY-x leaked", EvalCase("a", "", "CANARY-x")) == 1.0
    assert contains("clean", EvalCase("a", "", "CANARY-x")) == 0.0


def test_fields_match_partial_credit():
    case = EvalCase("a", "", {"present": ["x", "y"], "absent": ["z"]})
    assert fields_match(["x", "y"], case) == 1.0
    assert fields_match(["x", "z"], case) == pytest.approx(1 / 3)


def test_baseline_gate_flags_regressions():
    report = run_suite("s", [EvalCase("a", 1, 1), EvalCase("b", 1, 2)], lambda c: 1, exact_match)
    assert compare_to_baseline(report, {"pass_rate": 0.5}) == []
    assert compare_to_baseline(report, {"pass_rate": 1.0}) != []


def test_load_jsonl_skips_comments_and_reports_bad_lines(tmp_path):
    good = tmp_path / "ok.jsonl"
    good.write_text('# comment\n{"id": "a", "input": 1, "expected": 2}\n\n')
    assert load_jsonl(good) == [EvalCase("a", 1, 2)]
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"input": 1}\n')
    with pytest.raises(ValueError, match="bad.jsonl:1"):
        load_jsonl(bad)


def test_smoke_dataset_passes_fully():
    # Assert the pass rate itself: main()'s exit code is 0 whenever no baseline exists.
    report = run_suite("smoke", load_jsonl(DATASET), _build(), fields_match)
    assert len(report.results) >= 6
    assert report.pass_rate == 1.0, [r.case_id for r in report.results if not r.passed]


def test_eval_cli_exits_clean_against_committed_baseline():
    assert main([]) == 0


def test_committed_baseline_is_sane():
    assert json.loads(BASELINE.read_text())["pass_rate"] == 1.0
