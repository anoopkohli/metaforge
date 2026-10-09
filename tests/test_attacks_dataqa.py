"""Ask data attacks with a planner that emits whatever the attacker wants."""

import pytest

from attacks.dataqa import ATTACKS, NAIVE_EXPECTED_TO_SUCCEED, run_dataqa_attack
from dataqa import NAIVE_ENV, NAIVE_VALUE


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_attack_is_blocked_in_secure_mode(attack_id, monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    assert run_dataqa_attack(attack_id) is False


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_naive_mode_outcome_matches_the_claim(attack_id, monkeypatch):
    monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    assert run_dataqa_attack(attack_id, naive=True) is (attack_id in NAIVE_EXPECTED_TO_SUCCEED)
