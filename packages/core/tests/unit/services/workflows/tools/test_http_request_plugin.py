# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the HTTP request workflow plugin's SSRF defenses.

Focuses on the resolve-once-and-pin behaviour that closes the DNS-rebinding
window: the connection must target the pre-validated IP (not the hostname,
which httpx would otherwise re-resolve at connect time), while preserving the
original Host header and TLS SNI so routing and certificate verification stay
correct.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext
from chaoscypher_core.services.workflows.tools.plugins.http_request_plugin import RequestPlugin


_PLUGIN_MODULE = "chaoscypher_core.services.workflows.tools.plugins.http_request_plugin"


def _context() -> ToolExecutionContext:
    """Minimal execution context (the plugin only reads llm_service for timeouts)."""
    return ToolExecutionContext(graph_manager=MagicMock(), llm_service=None)


class _FakeResponse:
    """Stand-in httpx.Response with a non-JSON body."""

    status_code = 200
    headers: dict[str, str] = {}
    is_success = True

    def json(self) -> object:
        raise ValueError("not json")

    @property
    def text(self) -> str:
        return "ok"


class _CapturingClient:
    """Fake httpx.AsyncClient capturing the request kwargs."""

    captured: dict[str, object] = {}
    init_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        _CapturingClient.init_kwargs = kwargs

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def request(self, **kwargs: object) -> _FakeResponse:
        _CapturingClient.captured = kwargs
        return _FakeResponse()


class TestHttpRequestPluginSsrf:
    """SSRF protection for the HTTP request plugin."""

    @pytest.mark.asyncio
    async def test_metadata_ip_blocked(self) -> None:
        result = await RequestPlugin().execute(
            {"url": "http://169.254.169.254/latest/meta-data/"}, _context()
        )
        assert result["success"] is False
        assert result["status"] == 0

    @pytest.mark.asyncio
    async def test_localhost_blocked_strict(self) -> None:
        """A hostname resolving to loopback is refused (strict policy)."""
        result = await RequestPlugin().execute({"url": "http://localhost:11434/"}, _context())
        assert result["success"] is False
        assert result["status"] == 0

    @pytest.mark.asyncio
    async def test_connection_pinned_to_validated_ip(self, monkeypatch) -> None:
        """The connection dials the validated IP, not the (re-resolvable) hostname.

        Host header is preserved for routing and TLS SNI pins to the real
        hostname so certificate verification still targets the named server.
        """
        # Force a known validated IP regardless of real DNS.
        monkeypatch.setattr(
            f"{_PLUGIN_MODULE}.resolve_pinned_ip",
            lambda url, strict: "203.0.113.7",
        )
        monkeypatch.setattr(f"{_PLUGIN_MODULE}.httpx.AsyncClient", _CapturingClient)

        result = await RequestPlugin().execute(
            {"url": "https://evil.example.com/data?q=1"}, _context()
        )

        assert result["success"] is True
        captured = _CapturingClient.captured
        # Pinned: connection target host is the validated IP, not the hostname.
        assert str(captured["url"].host) == "203.0.113.7"
        # Path and query preserved.
        assert captured["url"].path == "/data"
        # Original authority preserved for request routing.
        assert captured["headers"]["Host"] == "evil.example.com"
        # TLS SNI / cert verification still target the real hostname.
        assert captured["extensions"]["sni_hostname"] == "evil.example.com"

    @pytest.mark.asyncio
    async def test_no_redirect_following(self, monkeypatch) -> None:
        """Redirects must not be followed (a 3xx Location could re-target SSRF)."""
        monkeypatch.setattr(
            f"{_PLUGIN_MODULE}.resolve_pinned_ip",
            lambda url, strict: "203.0.113.7",
        )
        monkeypatch.setattr(f"{_PLUGIN_MODULE}.httpx.AsyncClient", _CapturingClient)

        await RequestPlugin().execute({"url": "https://example.com/"}, _context())

        assert _CapturingClient.init_kwargs.get("follow_redirects") is False
