"""The stage-2 attack suite must be green in secure mode and red (where expected) in naive mode."""

import pytest

pytest.importorskip("mcp")

from attacks.mcp import ATTACKS, NAIVE_EXPECTED_TO_SUCCEED, run_attack  # noqa: E402
from mcp_server.tools import NAIVE_ENV, NAIVE_VALUE  # noqa: E402


@pytest.mark.parametrize("attack_id", sorted(ATTACKS))
def test_attack_fails_in_secure_mode(attack_id, monkeypatch):
    monkeypatch.delenv(NAIVE_ENV, raising=False)
    assert run_attack(attack_id) is False, f"{attack_id} succeeded against the secure server"


@pytest.mark.parametrize("attack_id", sorted(NAIVE_EXPECTED_TO_SUCCEED))
def test_attack_succeeds_in_naive_mode(attack_id, monkeypatch):
    # If this fails, the attack is not actually testing what it claims to test.
    monkeypatch.setenv(NAIVE_ENV, NAIVE_VALUE)
    assert run_attack(attack_id, naive=True) is True


def test_suite_covers_the_threat_model():
    assert len(ATTACKS) >= 12
    assert NAIVE_EXPECTED_TO_SUCCEED <= set(ATTACKS)
