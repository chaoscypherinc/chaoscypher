"""Keep the web-adapter unit tests off live DNS.

``WebScraper`` vets every URL through ``validate_url_safety(strict=True)``,
which resolves the host and fails closed when resolution fails. These tests
fetch ``https://example.com/...`` against mocked transports, so without this
stub they pass or fail on whether the machine running them has working DNS.
"""

from __future__ import annotations

import ipaddress

import pytest

from chaoscypher_core.utils import url_safety


# example.com's public address (a globally routable IP, as the strict policy requires).
_EXAMPLE_COM_IP = ipaddress.ip_address("93.184.215.14")


@pytest.fixture(autouse=True)
def _resolve_example_com_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve ``example.com`` without DNS; every other host resolves as usual."""
    real_resolve_all = url_safety._resolve_all

    def _resolve_all(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        if hostname == "example.com":
            return [_EXAMPLE_COM_IP]
        return real_resolve_all(hostname)

    monkeypatch.setattr(url_safety, "_resolve_all", _resolve_all)
