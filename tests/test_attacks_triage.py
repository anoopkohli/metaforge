"""Triage attacks with a model that obeys every injected instruction: blocked in secure mode, and
each one succeeds in naive mode (proving the attack can detect the failure it targets)."""

import pytest

from attacks.triage import ATTACKS, run_triage_attack
from triage.tools import NAIVE_ENV, NAIVE_VALUE


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_attack_is_blocked_in_secure_mode(attack_id, monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    assert run_triage_attack(attack_id) is False


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_attack_succeeds_in_naive_mode(attack_id, monkeypatch):
    monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    monkeypatch.setenv("METAFORGE_UNSAFE_NAIVE_RAG", "disable-tenant-filters")
    assert run_triage_attack(attack_id, naive=True) is True
