# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The 14-domain CLI list is defined once and reused by add/extract/confirm."""

from __future__ import annotations

from chaoscypher_cli.commands.source.add import add
from chaoscypher_cli.commands.source.extract import extract_cmd
from chaoscypher_cli.sources.domains import ADD_DOMAIN_CHOICES, EXTRACT_DOMAIN_CHOICES


# The 14 domains plus the two sentinels.
_CORE_DOMAINS = [
    "generic",
    "technical",
    "scientific",
    "medical",
    "legal",
    "financial",
    "news",
    "educational",
    "biographical",
    "historical",
    "literary",
    "philosophical",
    "political",
    "theological",
]


def test_add_choices_include_auto_and_all_domains() -> None:
    # `add` defaults to "auto", so "auto" is a valid choice.
    assert ADD_DOMAIN_CHOICES[0] == "auto"
    for d in _CORE_DOMAINS:
        assert d in ADD_DOMAIN_CHOICES


def test_extract_choices_include_auto_and_all_domains() -> None:
    assert "auto" in EXTRACT_DOMAIN_CHOICES
    for d in _CORE_DOMAINS:
        assert d in EXTRACT_DOMAIN_CHOICES


def _domain_choices(cmd: object) -> list[str]:
    for p in cmd.params:  # type: ignore[attr-defined]
        if p.name == "domain":
            return list(p.type.choices)  # type: ignore[attr-defined]
    raise AssertionError("no --domain option")


def test_add_command_uses_shared_constant() -> None:
    assert _domain_choices(add) == list(ADD_DOMAIN_CHOICES)


def test_extract_command_uses_shared_constant() -> None:
    assert _domain_choices(extract_cmd) == list(EXTRACT_DOMAIN_CHOICES)
