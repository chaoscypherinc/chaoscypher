# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``looks_like_secret``: memories never hold keys or passwords (spec decision 2)."""

from __future__ import annotations

import pytest

from chaoscypher_core.mcp.secrets import SECRET_PATTERNS, looks_like_secret


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...", "private key"),  # pragma: allowlist secret
        ("the key is AKIAIOSFODNN7EXAMPLE ok", "AWS access key"),  # pragma: allowlist secret
        ("use sk-abcdefghijklmnopqrstuvwxyz123456 for that", "API key"),
        (
            "token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD",  # pragma: allowlist secret
            "GitHub token",
        ),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc", "JWT"),  # pragma: allowlist secret
        ("my password = hunter2hunter2", "credential"),
        ("api_key: 0123456789abcdef", "credential"),
    ],
)
def test_each_pattern_names_its_kind(text: str, kind: str) -> None:
    assert looks_like_secret(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        "my password is strong",
        "the token of friendship",
        " ".join(["word"] * 40),
        "Pierre moved to Lyon in the spring of 2026 and keeps bees.",
    ],
)
def test_plain_text_is_not_a_secret(text: str) -> None:
    assert looks_like_secret(text) is None


def test_the_pattern_list_is_the_one_source() -> None:
    assert len(SECRET_PATTERNS) == 6
    assert next(kind for kind, _ in SECRET_PATTERNS) == "private key"
