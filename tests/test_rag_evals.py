import json

from evals import EvalCase, load_jsonl, run_suite
from evals.rag_suite import BASELINE, DATASET, build, rag_score


def test_rag_replay_suite_matches_its_baseline():
    report = run_suite("rag", load_jsonl(DATASET), build("replay"), rag_score)
    baseline = json.loads(BASELINE.read_text())["pass_rate"]
    assert round(report.pass_rate, 4) == baseline  # deterministic: any change must be deliberate
    assert all(r.score > 0 for r in report.results), "a zero score means a leak or a crash"


def test_rag_dataset_covers_every_document_and_several_tenants():
    cases = load_jsonl(DATASET)
    assert len(cases) >= 12
    assert len({c.expected["doc"] for c in cases}) == 6
    assert len({c.input["tenant_index"] for c in cases}) >= 3


def _out(**over) -> dict:
    base = {
        "tenant_id": "fjord",
        "answer": "The limit is 1000 dollars.",
        "citations": ["expense_policy#0"],
        "retrieved_docs": ["expense_policy"],
        "hit_tenants": ["fjord"],
        "foreign_canary_in_answer": False,
        "fact_value": "1000",
    }
    return {**base, **over}


CASE = EvalCase("x", {}, {"doc": "expense_policy", "fact": "expense_limit"})


def test_rag_score_full_and_partial_credit():
    assert rag_score(_out(), CASE) == 1.0
    assert rag_score(_out(answer="It depends."), CASE) == 2 / 3


def test_rag_score_is_zero_on_any_leak():
    assert rag_score(_out(hit_tenants=["cinder", "fjord"]), CASE) == 0.0
    assert rag_score(_out(foreign_canary_in_answer=True), CASE) == 0.0
