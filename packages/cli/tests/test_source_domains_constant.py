# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI domain list is defined once, reused by add/extract/confirm, and
kept in lockstep with the core builtin domain registry.

``PACK_NAMES`` is pinned to the registry's packs (``role == "pack"``); the
floor is excluded because every source has it. ``DOMAIN_NAMES`` keeps the
floor (``generic``) because ``--pack generic`` and the confirm prompt's
``generic`` choose the floor alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

import chaoscypher_core.services.sources.engine.extraction.domains as _domains_pkg
from chaoscypher_cli.commands.source.add import add
from chaoscypher_cli.commands.source.confirm import confirm_cmd
from chaoscypher_cli.commands.source.extract import extract_cmd
from chaoscypher_cli.sources.domains import (
    ADD_DOMAIN_CHOICES,
    DOMAIN_NAMES,
    EXTRACT_DOMAIN_CHOICES,
    PACK_NAMES,
    chosen_packs,
)


def _builtin_registry_roles() -> dict[str, str]:
    """``{name: role}`` of the builtin JSON-LD plugins in core."""
    plugins_dir = Path(_domains_pkg.__file__).parent / "plugins"
    roles: dict[str, str] = {}
    for config_path in plugins_dir.glob("*.jsonld"):
        with config_path.open(encoding="utf-8") as fh:
            config = json.load(fh)
        roles[config["name"]] = config.get("role", "pack")
    return roles


def _builtin_registry_domains() -> set[str]:
    """Domain names declared by the builtin JSON-LD plugins in core."""
    return set(_builtin_registry_roles())


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


def test_pack_names_are_the_registrys_packs_floor_excluded() -> None:
    """Every ``role == "pack"`` plugin, and not the floor every source has."""
    roles = _builtin_registry_roles()
    assert set(PACK_NAMES) == {name for name, role in roles.items() if role == "pack"}
    assert {name for name, role in roles.items() if role == "floor"} == {"generic"}
    assert "generic" not in PACK_NAMES


def _domain_choices(cmd: object) -> list[str]:
    for p in cmd.params:  # type: ignore[attr-defined]
        if p.name == "packs":
            assert set(p.opts) == {"--pack", "--domain"}  # --domain kept as the alias
            assert p.multiple
            return list(p.type.choices)
    raise AssertionError("no --pack option")


def test_add_command_uses_shared_constant() -> None:
    assert _domain_choices(add) == list(ADD_DOMAIN_CHOICES)


def test_extract_command_uses_shared_constant() -> None:
    assert _domain_choices(extract_cmd) == list(EXTRACT_DOMAIN_CHOICES)


def test_confirm_command_uses_shared_constant() -> None:
    assert _domain_choices(confirm_cmd) == list(DOMAIN_NAMES)


@pytest.mark.parametrize(
    ("values", "name"),
    [
        ((), None),  # not given
        (("auto",), "auto"),
        (("generic",), "generic"),  # the floor alone
        (("legal",), "legal"),
        (("literary", "historical"), "literary+historical"),  # the order given
        (("literary", "generic"), "literary"),  # the floor is implied
    ],
)
def test_chosen_packs_is_the_one_name_form(values: tuple[str, ...], name: str | None) -> None:
    assert chosen_packs(values) == name


def test_auto_mixed_with_packs_is_refused() -> None:
    with pytest.raises(click.BadParameter, match="not both"):
        chosen_packs(("auto", "legal"))
