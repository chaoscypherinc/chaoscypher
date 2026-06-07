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
