# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``host_allowed``: the DNS-rebinding rule Cortex and the CLI fallback server share."""

from __future__ import annotations

import pytest

from chaoscypher_core.utils.host_header import host_allowed, request_hostname


DEFAULT = ["localhost", "127.0.0.1", "::1"]


@pytest.mark.parametrize(
    ("header", "hostname"),
    [
        ("localhost:8080", "localhost"),
        ("LocalHost", "localhost"),
        ("[::1]:8080", "::1"),
        ("[::1]", "::1"),
        ("127.0.0.1", "127.0.0.1"),
    ],
)
def test_request_hostname_drops_port_brackets_and_case(header: str, hostname: str) -> None:
    assert request_hostname(header) == hostname


@pytest.mark.parametrize("header", ["localhost:8080", "127.0.0.1:80", "[::1]:8080"])
def test_a_listed_host_is_allowed(header: str) -> None:
    assert host_allowed(header, allowed_hosts=DEFAULT, allow_external_access=False)


@pytest.mark.parametrize("header", ["evil.example:8080", "localhost.evil.example", ""])
def test_a_rebinding_name_or_no_host_is_refused(header: str) -> None:
    assert not host_allowed(header, allowed_hosts=DEFAULT, allow_external_access=False)


def test_external_access_or_a_star_allows_any_host() -> None:
    assert host_allowed("evil.example", allowed_hosts=DEFAULT, allow_external_access=True)
    assert host_allowed("evil.example", allowed_hosts=["*"], allow_external_access=False)
