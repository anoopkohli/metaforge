"""Every RAG attack, in every configuration, must match the project's claim (`expected_success`)."""

import pytest

from attacks.rag import ATTACKS, CONFIGS, applies, expected_success, run_rag_attack
from rag import NAIVE_ENV, NAIVE_VALUE

CASES = [
    pytest.param(
        attack,
        strategy,
        naive,
        rls,
        id=f"{attack}-{strategy}-{'naive' if naive else 'secure'}{'' if rls else '-norls'}",
        marks=[pytest.mark.pg] if strategy == "pooled" else [],
    )
    for attack in sorted(ATTACKS)
    for strategy, naive, rls in CONFIGS
    if applies(attack, strategy)
]


@pytest.mark.parametrize("attack,strategy,naive,rls", CASES)
def test_attack_outcome_matches_the_claim(attack, strategy, naive, rls, monkeypatch, request):
    if strategy == "pooled":
        request.getfixturevalue("pg_dsns")  # skips (or fails in CI) without Postgres
    if naive:
        monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    else:
        monkeypatch.delenv(NAIVE_ENV, raising=False)
    want = expected_success(attack, strategy, naive, rls)
    got = run_rag_attack(attack, strategy, naive=naive, rls=rls)
    assert got == want, f"{attack} {'succeeded' if got else 'was blocked'}; expected the opposite"


def test_secure_mode_blocks_everything_by_claim():
    for attack in ATTACKS:
        for strategy, naive, rls in CONFIGS:
            if not naive and applies(attack, strategy) and attack != "rls-direct-sql":
                assert expected_success(attack, strategy, naive, rls) is False


def test_naive_mode_has_something_to_find():
    # If naive mode broke nothing, the suite would prove nothing.
    for strategy, naive, rls in CONFIGS:
        if naive:
            assert any(expected_success(a, strategy, naive, rls) for a in ATTACKS)
