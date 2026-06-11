# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI domain list is defined once, reused by add/extract/confirm, and
kept in lockstep with the core builtin domain registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import chaoscypher_core.services.sources.engine.extraction.domains as _domains_pkg
from chaoscypher_cli.commands.source.add import add
from chaoscypher_cli.commands.source.extract import extract_cmd
from chaoscypher_cli.sources.domains import (
    ADD_DOMAIN_CHOICES,
    DOMAIN_NAMES,
    EXTRACT_DOMAIN_CHOICES,
)


def _builtin_registry_domains() -> set[str]:
    """Domain names declared by the builtin JSON-LD plugins in core."""
    plugins_dir = Path(_domains_pkg.__file__).parent / "plugins"
    names: set[str] = set()
    for config_path in plugins_dir.glob("*.jsonld"):
        with config_path.open(encoding="utf-8") as fh:
            names.add(json.load(fh)["name"])
    return names


def test_domain_names_match_builtin_registry() -> None:
    """A domain plugin added to (or removed from) core without updating
    DOMAIN_NAMES is invisible to ``--domain`` and the confirm prompt —
    exactly how cybersecurity/design/intelligence/investigation/reference
    went missing from the CLI for a month.
    """
    registry = _builtin_registry_domains()
    assert registry, "no builtin domain plugins found — path layout changed?"
    assert set(DOMAIN_NAMES) == registry


def test_domain_names_have_no_duplicates_and_generic_first() -> None:
    assert len(set(DOMAIN_NAMES)) == len(DOMAIN_NAMES)
    # ``generic`` is the fallback domain and leads the interactive prompt.
    assert DOMAIN_NAMES[0] == "generic"


def test_add_choices_include_auto_and_all_domains() -> None:
    # `add` defaults to "auto", so "auto" is a valid choice.
    assert ADD_DOMAIN_CHOICES[0] == "auto"
    for d in DOMAIN_NAMES:
        assert d in ADD_DOMAIN_CHOICES


def test_extract_choices_include_auto_and_all_domains() -> None:
    assert "auto" in EXTRACT_DOMAIN_CHOICES
    for d in DOMAIN_NAMES:
        assert d in EXTRACT_DOMAIN_CHOICES


def _domain_choices(cmd: object) -> list[str]:
    for p in cmd.params:  # type: ignore[attr-defined]
        if p.name == "domain":
            return list(p.type.choices)
    raise AssertionError("no --domain option")


def test_add_command_uses_shared_constant() -> None:
    assert _domain_choices(add) == list(ADD_DOMAIN_CHOICES)


def test_extract_command_uses_shared_constant() -> None:
    assert _domain_choices(extract_cmd) == list(EXTRACT_DOMAIN_CHOICES)
