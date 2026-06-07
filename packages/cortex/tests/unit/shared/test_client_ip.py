# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for client_ip — proxy-aware client IP resolution.

The forwarded headers (``X-Real-IP`` / ``X-Forwarded-For``) are honoured only
when the request also carries a valid ``X-Auth-Edge-Token`` (injected by the
trusted nginx edge) or the app is in ``dev_mode``. A direct-to-cortex attacker
cannot mint that token, so they cannot spoof their source IP to poison a
rate-limit bucket or forge an audit-log origin.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from starlette.requests import Request

from chaoscypher_core import app_config
from chaoscypher_core.app_config import LocalAuthSettings, Settings
from chaoscypher_cortex.shared.utils.client_ip import client_ip


_EDGE_TOKEN = "edge-secret"
_EDGE_HEADER = (b"x-auth-edge-token", _EDGE_TOKEN.encode())


def _request(headers: list[tuple[bytes, bytes]], client: tuple[str, int] | None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/test",
            "headers": headers,
            "query_string": b"",
            "client": client,
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _trust_edge(monkeypatch: pytest.MonkeyPatch, *, dev_mode: bool = False) -> None:
    """Point get_settings() at a Settings with a known configured edge token."""
    settings = Settings(
        dev_mode=dev_mode,
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr(_EDGE_TOKEN)),
    )
    monkeypatch.setattr(app_config, "get_settings", lambda: settings)


# --- Headers honoured only when edge-verified -------------------------------


def test_trusts_x_real_ip_with_valid_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request(
        [(b"x-real-ip", b"203.0.113.10"), (b"x-forwarded-for", b"198.51.100.5"), _EDGE_HEADER],
        ("10.9.9.9", 12345),
    )
    assert client_ip(req) == "203.0.113.10"


def test_trusts_leftmost_forwarded_for_with_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request(
        [(b"x-forwarded-for", b"198.51.100.5, 10.0.0.1, 10.0.0.2"), _EDGE_HEADER],
        ("10.9.9.9", 12345),
    )
    assert client_ip(req) == "198.51.100.5"


def test_strips_whitespace_from_forwarded_for(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request(
        [(b"x-forwarded-for", b"  198.51.100.5  ,  10.0.0.1"), _EDGE_HEADER],
        ("10.9.9.9", 12345),
    )
    assert client_ip(req) == "198.51.100.5"


def test_dev_mode_trusts_header_without_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch, dev_mode=True)
    req = _request([(b"x-real-ip", b"203.0.113.10")], ("10.9.9.9", 12345))
    assert client_ip(req) == "203.0.113.10"


# --- Spoof rejection (no edge token, not dev_mode) --------------------------


def test_ignores_spoofed_x_real_ip_without_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """An attacker-set X-Real-IP is ignored; the real TCP peer is used."""
    _trust_edge(monkeypatch)  # token configured server-side, but request omits it
    req = _request([(b"x-real-ip", b"203.0.113.10")], ("10.9.9.9", 12345))
    assert client_ip(req) == "10.9.9.9"


def test_ignores_spoofed_forwarded_for_without_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request([(b"x-forwarded-for", b"203.0.113.10")], ("10.9.9.9", 12345))
    assert client_ip(req) == "10.9.9.9"


def test_ignores_spoofed_header_with_wrong_edge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request(
        [(b"x-real-ip", b"203.0.113.10"), (b"x-auth-edge-token", b"wrong")],
        ("10.9.9.9", 12345),
    )
    assert client_ip(req) == "10.9.9.9"


# --- Fallbacks --------------------------------------------------------------


def test_falls_back_to_client_host_when_headers_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request([_EDGE_HEADER], ("10.9.9.9", 12345))
    assert client_ip(req) == "10.9.9.9"


def test_returns_unknown_when_no_client_and_no_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request([], None)
    assert client_ip(req) == "unknown"


def test_blank_forwarded_for_falls_through_to_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _trust_edge(monkeypatch)
    req = _request([(b"x-forwarded-for", b" , 10.0.0.1"), _EDGE_HEADER], ("10.9.9.9", 12345))
    assert client_ip(req) == "10.9.9.9"
