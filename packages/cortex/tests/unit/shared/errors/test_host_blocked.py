# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the host-blocked HTML payload builder."""

from __future__ import annotations

from chaoscypher_cortex.shared.errors.host_blocked import build_host_blocked_html


def test_includes_attempted_host_in_details() -> None:
    kwargs = build_host_blocked_html("192.168.1.20", ["localhost", "127.0.0.1"])
    assert kwargs["title"]
    assert kwargs["lead"]
    assert any("192.168.1.20" in v for _, v in kwargs["details"])


def test_lists_all_allowed_hosts_in_details() -> None:
    allowed = ["localhost", "127.0.0.1", "::1"]
    kwargs = build_host_blocked_html("example.com", allowed)
    joined = " ".join(v for _, v in kwargs["details"])
    for host in allowed:
        assert host in joined


def test_fix_steps_reference_settings_path() -> None:
    kwargs = build_host_blocked_html("example.com", ["localhost"])
    fix_joined = " ".join(kwargs["fix"])
    assert "localhost" in fix_joined
    assert "Settings" in fix_joined or "settings" in fix_joined


def test_http_label_is_421() -> None:
    kwargs = build_host_blocked_html("example.com", ["localhost"])
    assert "421" in kwargs["http_label"]
