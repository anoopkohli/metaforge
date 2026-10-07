"""Minimal signed bearer tokens (stdlib only).

Format: base64url(json claims) + "." + base64url(HMAC-SHA256). This is a deliberately small
stand-in for a real identity provider. Its job in this project is to be the *only* source of a
tenant ID. Swap it for OIDC/JWT later without touching callers: they only see a TenantContext.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from core.tenancy.context import TenantContext


class AuthError(Exception):
    """Token is malformed, forged, expired or missing required claims."""


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(secret: bytes, payload: str) -> str:
    return _b64e(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def issue_token(
    secret: str | bytes,
    tenant_id: str,
    subject: str = "user",
    roles: tuple[str, ...] = (),
    ttl_seconds: int = 3600,
    now: float | None = None,
) -> str:
    key = secret.encode() if isinstance(secret, str) else secret
    if not key:
        raise AuthError("empty signing secret")
    issued = time.time() if now is None else now
    claims = {
        "tid": tenant_id,
        "sub": subject,
        "roles": sorted(roles),
        "iat": int(issued),
        "exp": int(issued + ttl_seconds),
    }
    payload = _b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_sign(key, payload)}"


def verify_token(secret: str | bytes, token: str, now: float | None = None) -> TenantContext:
    """Verify signature and expiry, then build the TenantContext. Never trusts anything unsigned."""
    key = secret.encode() if isinstance(secret, str) else secret
    if not key:
        raise AuthError("empty signing secret")
    try:
        payload, signature = token.split(".", 1)
    except (ValueError, AttributeError) as exc:
        raise AuthError("malformed token") from exc

    if not hmac.compare_digest(_sign(key, payload), signature):
        raise AuthError("bad signature")

    try:
        claims = json.loads(_b64d(payload))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("unreadable claims") from exc

    current = time.time() if now is None else now
    exp = claims.get("exp")
    if not isinstance(exp, int | float) or current >= exp:
        raise AuthError("token expired")

    tenant_id = claims.get("tid")
    if not tenant_id:
        raise AuthError("token has no tenant claim")

    try:
        return TenantContext(
            tenant_id=tenant_id,
            subject=str(claims.get("sub", "user")),
            roles=frozenset(claims.get("roles", [])),
        )
    except ValueError as exc:
        raise AuthError(str(exc)) from exc
