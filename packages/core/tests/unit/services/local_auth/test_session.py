# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for session cookie encode/decode."""

from __future__ import annotations

import time

import pytest

from chaoscypher_core.services.local_auth.errors import InvalidSessionCookie
from chaoscypher_core.services.local_auth.session import (
    SessionPayload,
    decode_session,
    encode_session,
)


SECRET = b"0123456789abcdef0123456789abcdef"


def test_roundtrip_returns_payload() -> None:
    cookie = encode_session("admin", session_epoch=5, ttl_seconds=60, secret=SECRET)
    payload = decode_session(cookie, secret=SECRET)
    assert isinstance(payload, SessionPayload)
    assert payload.username == "admin"
    assert payload.session_epoch == 5


def test_expired_cookie_raises() -> None:
    cookie = encode_session("admin", session_epoch=1, ttl_seconds=-1, secret=SECRET)
    with pytest.raises(InvalidSessionCookie, match="expired"):
        decode_session(cookie, secret=SECRET)


def test_tampered_signature_raises() -> None:
    cookie = encode_session("admin", session_epoch=1, ttl_seconds=60, secret=SECRET)
    parts = cookie.rsplit(".", 1)
    tampered = parts[0] + "." + ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
    with pytest.raises(InvalidSessionCookie, match="signature"):
        decode_session(tampered, secret=SECRET)


def test_wrong_secret_raises() -> None:
    cookie = encode_session("admin", session_epoch=1, ttl_seconds=60, secret=SECRET)
    with pytest.raises(InvalidSessionCookie):
        decode_session(cookie, secret=b"x" * 32)


def test_malformed_cookie_raises() -> None:
    with pytest.raises(InvalidSessionCookie):
        decode_session("not-a-valid-cookie", secret=SECRET)
    with pytest.raises(InvalidSessionCookie):
        decode_session("only.two.three", secret=SECRET)


def test_empty_cookie_raises() -> None:
    with pytest.raises(InvalidSessionCookie):
        decode_session("", secret=SECRET)


def test_payload_is_sliding_aware() -> None:
    """encode_session uses 'now' at encode time; decode_session checks exp against now."""
    cookie = encode_session("admin", session_epoch=1, ttl_seconds=2, secret=SECRET)
    time.sleep(2.1)
    with pytest.raises(InvalidSessionCookie, match="expired"):
        decode_session(cookie, secret=SECRET)


def test_username_with_dots_or_colons() -> None:
    """Username with special chars must roundtrip cleanly."""
    cookie = encode_session("user.name:weird", session_epoch=1, ttl_seconds=60, secret=SECRET)
    payload = decode_session(cookie, secret=SECRET)
    assert payload.username == "user.name:weird"


def test_payload_exposes_expires_at() -> None:
    """SessionPayload carries expires_at unix ts for the service layer to inspect."""
    cookie = encode_session("admin", session_epoch=1, ttl_seconds=60, secret=SECRET)
    payload = decode_session(cookie, secret=SECRET)
    now = int(time.time())
    assert now <= payload.expires_at <= now + 60


def _sign(payload: dict[str, object]) -> str:
    """Build a signature-valid cookie for an arbitrary (possibly malformed) payload."""
    import hmac
    import json
    from hashlib import sha256

    from chaoscypher_core.services.local_auth.session import _b64

    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64(hmac.new(SECRET, body.encode("ascii"), sha256).digest())
    return f"{body}.{sig}"


def test_signed_payload_missing_username_raises_invalid_cookie() -> None:
    """A signed but structurally-invalid payload must raise InvalidSessionCookie, not KeyError."""
    future = int(time.time()) + 60
    cookie = _sign({"e": 1, "x": future})  # missing "u"
    with pytest.raises(InvalidSessionCookie, match="payload"):
        decode_session(cookie, secret=SECRET)


def test_signed_payload_non_int_epoch_raises_invalid_cookie() -> None:
    """A signed payload with a non-int session_epoch must raise InvalidSessionCookie, not ValueError."""
    future = int(time.time()) + 60
    cookie = _sign({"u": "admin", "e": "not-an-int", "x": future})
    with pytest.raises(InvalidSessionCookie, match="payload"):
        decode_session(cookie, secret=SECRET)


def test_non_ascii_cookie_raises_invalid_session_cookie() -> None:
    """A non-ASCII cookie must raise InvalidSessionCookie, not escape as a 500.

    ``body.encode("ascii")`` raises UnicodeEncodeError and
    ``hmac.compare_digest`` raises TypeError on non-ASCII input, and both run
    before any signature check. Neither is an InvalidSessionCookie, so callers
    that catch only that exception missed them and the request reached the
    FastAPI catch-all — on ``GET /api/v1/auth/status``, which needs no
    credential. This is a 500/log-amplification fix, not an auth bypass:
    no signature was ever accepted on this path.
    """
    secret = b"x" * 32

    # Non-ASCII in the body half (UnicodeEncodeError before the fix).
    with pytest.raises(InvalidSessionCookie):
        decode_session("\x80abc.\x80def", secret)

    # Non-ASCII in the signature half (TypeError before the fix).
    with pytest.raises(InvalidSessionCookie):
        decode_session("abc.\xe9def", secret)
