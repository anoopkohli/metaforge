import pytest

from core.auth import AuthError, issue_token, verify_token


def test_roundtrip(secret):
    token = issue_token(secret, "acme", subject="alice", roles=("admin",))
    ctx = verify_token(secret, token)
    assert ctx.tenant_id == "acme"
    assert ctx.subject == "alice"
    assert ctx.roles == frozenset({"admin"})


def test_wrong_secret_rejected(secret):
    token = issue_token(secret, "acme")
    with pytest.raises(AuthError):
        verify_token("different-secret", token)


def test_tampered_payload_rejected(secret):
    # Swap the payload for another tenant's but keep the original signature.
    good = issue_token(secret, "acme")
    other = issue_token(secret, "borealis")
    forged = other.split(".")[0] + "." + good.split(".")[1]
    with pytest.raises(AuthError):
        verify_token(secret, forged)


def test_expired_rejected(secret):
    token = issue_token(secret, "acme", ttl_seconds=10, now=1_000)
    assert verify_token(secret, token, now=1_005).tenant_id == "acme"
    with pytest.raises(AuthError):
        verify_token(secret, token, now=1_010)


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "....", ".", "notbase64.notbase64"])
def test_garbage_rejected(secret, junk):
    with pytest.raises(AuthError):
        verify_token(secret, junk)


def test_non_string_token_rejected(secret):
    with pytest.raises(AuthError):
        verify_token(secret, None)  # type: ignore[arg-type]


def test_empty_secret_rejected():
    with pytest.raises(AuthError):
        issue_token("", "acme")
    with pytest.raises(AuthError):
        verify_token("", "x.y")


def test_invalid_tenant_claim_rejected(secret):
    # Signed correctly, but the tenant id fails validation: still no context.
    token = issue_token(secret, "BAD TENANT")
    with pytest.raises(AuthError):
        verify_token(secret, token)
