# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Help-text consistency checks across every CLI subcommand.

These tests walk the entire Click command tree, render ``--help`` for
each leaf and group, and run text-level assertions on the result. They
exist because:

* **Bug 5** ``source list --json`` was advertised by the umbrella
  ``source --help`` quick reference ("--format json") but the underlying
  command didn't accept ``--json``. Documentation said one thing, code
  did another, no test caught the drift.

* **Bug 6** ``source list`` printed "Add files with: chaoscypher ingest
  add <file>" on an empty database, even though the ``ingest`` command
  group was renamed to ``source`` long ago. A user copy-pasting the
  suggestion got "No such command: ingest" with no breadcrumbs back.

Every subcommand's help text is a contract with the user; these tests
pin the contract.

LazyGroup wrinkle
-----------------
``chaoscypher_cli.lazy.LazyGroup`` defers subcommand imports until a
command is actually invoked (it checks ``sys.argv`` to decide). During a
pytest invocation ``sys.argv`` is pytest's, so without help the lazy
commands return stubs whose subcommands aren't visible. The tests below
set ``sys.argv`` to the synthetic ``chaoscypher <path> --help`` form
before each ``CliRunner().invoke`` so the real groups load and their
``list_commands`` is meaningful.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from click.testing import CliRunner


# Top-level lazy commands as registered in __main__.LAZY_COMMANDS, plus
# any non-lazy groups that ship alongside. Keep this list mirroring the
# real registry; the suite below sanity-checks that they're all still
# wired up so an accidental rename or deletion fails loudly.
TOP_LEVEL_COMMANDS: list[str] = [
    "benchmark",
    "chat",
    "completions",
    "compose",
    "config",
    "db",
    "diagnostics",
    "graph",
    "health",
    "lexicon",
    "mcp",
    "pull",
    "push",
    "render-orchestration",
    "serve",
    "setup",
    "source",
    "upgrade",
]


def _help(monkeypatch: pytest.MonkeyPatch, *path: str) -> Any:
    """Render ``chaoscypher <*path> --help`` through CliRunner.

    Sets ``sys.argv`` to mirror the same path so LazyGroup's
    ``_is_being_invoked`` heuristic loads the real subcommands rather
    than returning stubs. Returns the ``CliRunner.Result``.
    """
    from chaoscypher_cli.__main__ import main

    monkeypatch.setattr(sys, "argv", ["chaoscypher", *path, "--help"])
    return CliRunner().invoke(main, [*path, "--help"])


@pytest.mark.parametrize("cmd", TOP_LEVEL_COMMANDS)
def test_top_level_command_help_renders(cmd: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``chaoscypher <cmd> --help`` must exit 0 with a Usage: line.

    Catches typos / unimportable lazy targets / busted ``cls=`` wiring.
    """
    result = _help(monkeypatch, cmd)

    assert result.exit_code == 0, (
        f"chaoscypher {cmd} --help exited {result.exit_code}\n"
        f"stdout: {result.output}\n"
        f"stderr: {result.stderr}"
    )
    assert "Usage:" in result.output, (
        f"chaoscypher {cmd} --help had no Usage: line.\noutput: {result.output[:400]}"
    )


@pytest.mark.parametrize("cmd", TOP_LEVEL_COMMANDS)
def test_top_level_help_does_not_reference_stale_ingest(
    cmd: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug 6 regression: the ``ingest`` group was renamed to ``source``.

    Any leftover ``chaoscypher ingest`` reference sends users to a dead
    end. Sweep all top-level help text. (Deeper nesting is covered in
    the sub-suite below.)
    """
    result = _help(monkeypatch, cmd)
    assert "chaoscypher ingest" not in result.output, (
        f"chaoscypher {cmd} --help references the stale "
        f"'chaoscypher ingest' command:\n{result.output}"
    )


@pytest.mark.parametrize("cmd", TOP_LEVEL_COMMANDS)
def test_top_level_help_does_not_leak_click_paragraph_escape(
    cmd: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""Click's ``\\b`` token suppresses paragraph reflow inside Click
    docstrings. If a literal ``\\b`` makes it into rendered output, the
    docstring was malformed and the user sees raw backslash-b sequences
    in their --help.
    """
    result = _help(monkeypatch, cmd)
    assert "\\b" not in result.output, (
        f"chaoscypher {cmd} --help leaks literal '\\b' sequence — fix "
        f"the docstring:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Source-group sub-commands — the most user-visible group, also where the
# original `ingest add` regression lived.
# ---------------------------------------------------------------------------

SOURCE_SUBCOMMANDS: list[str] = [
    "add",
    "confirm",
    "delete",
    "extract",
    "get",
    "list",
    "quality",
    "rebuild-search",
    "search",
]


@pytest.mark.parametrize("sub", SOURCE_SUBCOMMANDS)
def test_source_subcommand_help_renders(sub: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _help(monkeypatch, "source", sub)
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("sub", SOURCE_SUBCOMMANDS)
def test_source_subcommand_help_does_not_reference_stale_ingest(
    sub: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _help(monkeypatch, "source", sub)
    assert "chaoscypher ingest" not in result.output, (
        f"chaoscypher source {sub} --help references the stale "
        f"'chaoscypher ingest' command:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Bug 5: cross-command --json consistency
# ---------------------------------------------------------------------------

# Commands that DO advertise JSON output and DO accept --json today. Test
# pins both halves of the contract: the help text mentions JSON AND the
# option is real. Drift in either direction (option removed without
# updating docs, or docs claim JSON but option is missing) fails the
# test. Adding a new command that supports JSON: add its path here.
COMMANDS_ADVERTISING_JSON: list[tuple[str, ...]] = [
    ("db", "info"),
    ("db", "list"),
    ("source", "add"),
    ("source", "search"),
    ("source", "list"),
]


@pytest.mark.parametrize("path", COMMANDS_ADVERTISING_JSON, ids=lambda p: "-".join(p))
def test_commands_documenting_json_actually_accept_it(
    path: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commands that advertise JSON output must accept ``--json`` OR
    ``--format json``.

    Two flag shapes coexist in the CLI today:

    * ``--json`` — boolean flag (db info, db list, source add). One
      output mode (JSON), simple.
    * ``--format [table|json|yaml]`` — choice flag (source list,
      source search). Multiple output modes, including YAML, in a
      single option.

    Both are valid; the bug we're catching is when help-text claims
    JSON output but neither option is wired up (so users get
    ``Error: No such option: --json``). New commands should prefer
    ``--json`` for simplicity unless they genuinely need a third
    output mode.
    """
    result = _help(monkeypatch, *path)
    assert result.exit_code == 0, result.output

    has_json_flag = "--json" in result.output
    has_format_json = "--format" in result.output and "json" in result.output

    assert has_json_flag or has_format_json, (
        f"chaoscypher {' '.join(path)} is on the JSON-supported list but "
        f"its --help exposes neither a ``--json`` flag nor a ``--format`` "
        f"choice that includes ``json``. Either wire up one of those "
        f"options or remove this command from COMMANDS_ADVERTISING_JSON. "
        f"Help output:\n{result.output}"
    )
