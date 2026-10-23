# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A fake web site behind the real, hardened WebScraper — no network at all.

``fake_site`` installs an ``httpx.MockTransport`` client as the scraper's
module-level cached client and resolves the test hosts without DNS, so code
under test runs the production path end to end: the strict SSRF check, the
pinned-IP dial (the transport sees the vetted IP plus the Host header),
redirect re-checks, the byte cap and the User-Agent.

Shared by every package's tests that fetch web pages (Core's site discovery
and import, the CLI's ``source add --site``, the MCP server's ``add_site``):
pytest's conftest discovery does not cross package boundaries, so a package
conftest imports the ``fake_site`` fixture from here.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

import chaoscypher_core.adapters.web.search as search_mod
from chaoscypher_core.services.sources.web import CrawlThrottle
from chaoscypher_core.utils import url_safety


PUBLIC_HOSTS = ("docs.example.com", "example.com", "other.example.org")
PRIVATE_HOST = "intranet.example.com"
_PUBLIC_IP = ipaddress.ip_address("93.184.215.14")
_PRIVATE_IP = ipaddress.ip_address("10.0.0.7")


@dataclass
class _Route:
    """One canned answer: status, content type, body and extra headers."""

    status: int
    content_type: str
    body: bytes
    headers: dict[str, str]


@dataclass
class FakeSite:
    """Routes keyed by logical URL; records every request the transport saw."""

    routes: dict[str, _Route] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)
    user_agents: list[str] = field(default_factory=list)
    #: Hosts whose every ``http://`` address answers a 301 to its ``https://`` twin.
    https_only: set[str] = field(default_factory=set)
    #: Called with each request before it is answered (a test raises from it).
    on_request: Callable[[httpx.Request], None] | None = None

    def add(
        self,
        url: str,
        body: str | bytes = "",
        *,
        content_type: str = "text/html; charset=utf-8",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Serve *body* at *url* (a logical ``scheme://host/path`` address)."""
        payload = body.encode("utf-8") if isinstance(body, str) else body
        self.routes[url] = _Route(status, content_type, payload, headers or {})

    def redirect(self, url: str, location: str, status: int = 302) -> None:
        """Answer *url* with a redirect to *location*."""
        self.add(url, "", status=status, headers={"location": location})

    def upgrade_to_https(self, host: str) -> None:
        """Answer every ``http://host/...`` request with a 301 to ``https://host/...``."""
        self.https_only.add(host)

    def page(self, url: str, title: str, links: list[str]) -> None:
        """An HTML page with a <title> and one anchor per link."""
        anchors = "".join(f'<li><a href="{href}">{href}</a></li>' for href in links)
        self.add(
            url,
            f"<html><head><title>{title}</title></head>"
            f"<body><nav><ul>{anchors}</ul></nav><p>Body of {title}.</p></body></html>",
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        """The transport: record the request, answer its route or a 404."""
        logical = f"{request.url.scheme}://{request.headers['host']}{request.url.raw_path.decode()}"
        self.requested.append(logical)
        self.user_agents.append(request.headers.get("user-agent", ""))
        if self.on_request is not None:
            self.on_request(request)
        if request.url.scheme == "http" and request.headers["host"] in self.https_only:
            secure = logical.replace("http://", "https://", 1)
            return httpx.Response(301, headers={"location": secure})
        route = self.routes.get(logical)
        if route is None:
            return httpx.Response(404, headers={"content-type": "text/html"}, text="not found")
        headers = {"content-type": route.content_type, **route.headers}
        return httpx.Response(route.status, headers=headers, content=route.body)


@pytest.fixture
def fake_site(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    """A fake site served to the real WebScraper; test hosts resolve offline."""
    site = FakeSite()

    def _resolve_all(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        if hostname in PUBLIC_HOSTS:
            return [_PUBLIC_IP]
        if hostname == PRIVATE_HOST:
            return [_PRIVATE_IP]
        return []

    monkeypatch.setattr(url_safety, "_resolve_all", _resolve_all)
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler), follow_redirects=False)
    monkeypatch.setattr(search_mod, "_client", client)
    return site


@dataclass
class FakeClock:
    """A monotonic clock that moves only when the throttle sleeps on it."""

    now: float = 0.0
    slept: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        """The current time."""
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Advance the clock instead of waiting."""
        self.slept.append(seconds)
        self.now += seconds

    def throttle(self, *, delay_seconds: float, concurrency: int = 2) -> CrawlThrottle:
        """A :class:`CrawlThrottle` that waits on this clock."""
        return CrawlThrottle(
            delay_seconds=delay_seconds, concurrency=concurrency, clock=self, sleep=self.sleep
        )


__all__ = ["PRIVATE_HOST", "PUBLIC_HOSTS", "FakeClock", "FakeSite", "fake_site"]
