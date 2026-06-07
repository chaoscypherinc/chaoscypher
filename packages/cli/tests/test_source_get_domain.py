# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from chaoscypher_cli.commands.source.get import _format_domain_row


def test_format_domain_row_with_version():
    label, detail = _format_domain_row(
        forced_domain=None, detected_domain="technical", domain_version="1.9.0", changed=False
    )
    assert "technical" in label
    assert "1.9.0" in label
    assert detail is None


def test_format_domain_row_stale_adds_warning():
    _, detail = _format_domain_row(
        forced_domain="technical", detected_domain=None, domain_version="1.9.0", changed=True
    )
    assert detail is not None
    assert "changed since extraction" in detail


def test_format_domain_row_no_version():
    label, detail = _format_domain_row(
        forced_domain=None, detected_domain=None, domain_version=None, changed=False
    )
    assert "auto" in label
    assert detail is None
