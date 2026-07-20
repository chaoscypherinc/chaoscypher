# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Stateless HMAC-SHA256 session cookies.

Format: base64url(json_payload) "." base64url(hmac_sha256(json_payload, secret))
Payload: {"u": username, "e": session_epoch, "x": exp_unix_ts}

No persistent session store — the cookie IS the session. Invalidation works by
bumping the session_epoch in credentials.json (password/username changes); any
cookie with a stale epoch is rejected by the service layer.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass
from hashlib import sha256

from chaoscypher_core.services.local_auth.errors import InvalidSessionCookie


@dataclass(frozen=True)
class SessionPayload:
    """Decoded session cookie claims returned by :func:`decode_session`."""

    username: str
    session_epoch: int
    expires_at: int


def encode_session(username: str, session_epoch: int, ttl_seconds: int, secret: bytes) -> str:
    """Return a signed session cookie value.

    Args:
        username: The authenticated username.
        session_epoch: Current session_epoch from the credentials file.
        ttl_seconds: Cookie lifetime. Negative values produce immediately-expired
            cookies (useful for tests).
        secret: HMAC secret (32+ bytes recommended).
    """
    exp = int(time.time()) + ttl_seconds
    payload = {"u": username, "e": session_epoch, "x": exp}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64(hmac.new(secret, body.encode("ascii"), sha256).digest())
    return f"{body}.{sig}"


def decode_session(cookie: str, secret: bytes) -> SessionPayload:
    """Verify signature + expiry. Raise InvalidSessionCookie on any failure.

    Does NOT verify session_epoch against the credentials file — the service
    layer does that so this module stays pure-logic.
    """
    if not cookie or cookie.count(".") != 1:
        raise InvalidSessionCookie("malformed")
    body, sig = cookie.split(".", 1)
    expected_sig = _b64(hmac.new(secret, body.encode("ascii"), sha256).digest())
    if not hmac.compare_digest(sig, expected_sig):
        raise InvalidSessionCookie("signature")
    try:
        payload = json.loads(_unb64(body))
    except Exception as exc:
        raise InvalidSessionCookie("payload") from exc
    if payload.get("x", 0) <= int(time.time()):
        raise InvalidSessionCookie("expired")
    try:
        return SessionPayload(
            username=str(payload["u"]),
            session_epoch=int(payload["e"]),
            expires_at=int(payload["x"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # A signature-valid cookie whose payload is missing "u"/"e" or carries a
        # non-int "e" must still raise InvalidSessionCookie, per this function's
        # documented contract — never leak a raw KeyError/ValueError.
        raise InvalidSessionCookie("payload") from exc


def _b64(data: bytes) -> str:
    """Encode bytes as URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    """Decode URL-safe base64, restoring any stripped padding."""
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)
