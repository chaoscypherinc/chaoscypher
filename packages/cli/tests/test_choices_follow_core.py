# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A ``click.Choice`` that lists a core type's values offers exactly those values.

Most are built from the type (``get_args(ElementKind)``); the ``mcp`` and ``mount``
commands write theirs out so ``--help`` and shell completion do not import core, and this
test is what keeps those copies in step. 0.7.0 shipped a hand-copied list that had fallen
behind its model (MCP ``create_template``'s ``property_type``); these are the CLI's copies.
"""

from __future__ import annotations

import importlib
from typing import Any, get_args

import click
import pytest

from chaoscypher_core.mcp.tools import TOOL_PROFILES
from chaoscypher_core.models import MCPMode


def _choices(module: str, command: str, option: str) -> list[str]:
    cmd = getattr(importlib.import_module(module), command)
    (param,) = [p for p in cmd.params if p.name == option]
    assert isinstance(param.type, click.Choice), f"{module}.{command} --{option}"
    return list(param.type.choices)


CHOICES: list[tuple[str, str, str, Any]] = [
    ("chaoscypher_cli.mcp.command", "mcp", "mode", list(get_args(MCPMode))),
    ("chaoscypher_cli.mcp.command", "mcp", "profile", list(TOOL_PROFILES)),
    ("chaoscypher_cli.commands.mount", "mount", "mode", list(get_args(MCPMode))),
]
"""The written-out copies; every other core-typed choice is built from its type."""


@pytest.mark.parametrize(("module", "command", "option", "expected"), CHOICES)
def test_the_choice_lists_its_core_types_values(
    module: str, command: str, option: str, expected: list[str]
) -> None:
    assert _choices(module, command, option) == expected
