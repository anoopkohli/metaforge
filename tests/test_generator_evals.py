import json

import pytest

from evals import EvalCase, load_jsonl, run_suite
from evals.generator_suite import BASELINE, DATASET, build, proposal_matches
from evals.run import main


def test_generator_replay_suite_passes_fully():
    report = run_suite("generator", load_jsonl(DATASET), build("replay"), proposal_matches)
    assert report.pass_rate == 1.0, [
        (r.case_id, r.score, r.error) for r in report.results if not r.passed
    ]


def test_generator_dataset_covers_the_threat_model():
    cases = load_jsonl(DATASET)
    assert len(cases) >= 15
    tags = {t for c in cases for t in c.tags}
    assert {"create", "override", "repair", "collision", "tenancy", "injection"} <= tags
    # Live mode must have enough cases to mean something.
    assert sum("replay-only" not in c.tags for c in cases) >= 8


def test_generator_baseline_is_committed():
    assert json.loads(BASELINE.read_text())["pass_rate"] == 1.0


def test_eval_cli_runs_every_suite_against_baselines(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "smoke:" in out and "generator:" in out


def _case(expected: dict) -> EvalCase:
    return EvalCase("x", {}, expected)


def _out(proposal: dict | None, ok: bool = True, attempts: int = 1, effective=None) -> dict:
    return {
        "ok": ok,
        "proposal": proposal,
        "attempts": attempts,
        "mode": "replay",
        "effective": effective,
    }


CREATE = {
    "action": "create_object",
    "object": {
        "name": "vehicle",
        "fields": [
            {"name": "plate", "type": "string", "required": True},
            {"name": "owner", "type": "reference", "reference_to": "employee"},
        ],
    },
}


def test_scorer_accepts_name_alternatives():
    case = _case(
        {
            "action": "create_object",
            "name": "car|vehicle",
            "fields": {"plate|plate_number": "string"},
        }
    )
    assert proposal_matches(_out(CREATE), case) == 1.0


def test_scorer_gives_partial_credit():
    case = _case({"action": "create_object", "name": "vehicle", "fields": {"plate": "integer"}})
    assert proposal_matches(_out(CREATE), case) == pytest.approx(2 / 3)


def test_scorer_checks_references_and_required():
    case = _case({"required": ["plate"], "references": {"owner": "employee"}})
    assert proposal_matches(_out(CREATE), case) == 1.0
    case = _case({"required": ["owner"], "references": {"owner": "fjord_asset"}})
    assert proposal_matches(_out(CREATE), case) == 0.0


def test_scorer_null_action_means_generation_must_fail():
    case = _case({"action": None})
    assert proposal_matches(_out(None, ok=False, attempts=3), case) == 1.0
    assert proposal_matches(_out(CREATE), case) == 0.0


def test_scorer_attempts_only_checked_in_replay_mode():
    case = _case({"action": "create_object", "replay_attempts": 1})
    assert proposal_matches(_out(CREATE, attempts=2), case) == 0.5
    live = {**_out(CREATE, attempts=2), "mode": "live"}
    assert proposal_matches(live, case) == 1.0
