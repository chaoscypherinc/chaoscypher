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

import re
import sys
from pathlib import Path
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
    "doctor",
    "graph",
    "health",
    "lexicon",
    "mcp",
    "memory",
    "mount",
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


# ---------------------------------------------------------------------------
# Retired flat command paths — whole-source sweep (generalized Bug 6)
# ---------------------------------------------------------------------------

# The command-tree reorg moved these groups under parents:
#
#   chaoscypher node|link|template|workflow|package → chaoscypher graph <...>
#   chaoscypher quality                             → chaoscypher source quality
#   chaoscypher login|search                        → chaoscypher lexicon <...>
#
# Any string still using the flat form (docstrings rendered by --help,
# Examples sections, runtime "next step" hints) copy-pastes to
# "No such command". The legitimate replacements all put the parent group
# name between "chaoscypher" and the subcommand (e.g. "chaoscypher graph
# node", "chaoscypher source quality", "chaoscypher lexicon login"), so
# they can never match this pattern; root aliases like "chaoscypher pull"
# / "chaoscypher push" and group-only forms like "chaoscypher lexicon
# list" aren't in the denylist at all.
_RETIRED_FLAT_PATH = re.compile(
    r"\bchaoscypher (node|link|template|workflow|quality|package|login|search)\b"
)


def test_no_retired_flat_command_paths_in_source() -> None:
    """No source string may reference a retired flat command path.

    Scans every Python file under ``src/chaoscypher_cli`` so the check
    covers help docstrings AND runtime echo/print hints alike.
    """
    import chaoscypher_cli

    src_root = Path(chaoscypher_cli.__file__).resolve().parent
    offenders: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if _RETIRED_FLAT_PATH.search(line):
                rel = py_file.relative_to(src_root)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Retired flat command paths found — these render in --help or "
        "runtime hints and copy-paste to 'No such command'. Replace with "
        "the registered paths (chaoscypher graph node|link|template|"
        "workflow|package ..., chaoscypher source quality ..., "
        "chaoscypher lexicon login|search ...):\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# The Click paragraph escape, across the WHOLE tree.
#
# ``test_top_level_help_does_not_leak_click_paragraph_escape`` above applies
# this assertion to the 20 hand-listed ``TOP_LEVEL_COMMANDS`` only, while this
# module's docstring claims the tests "walk the entire Click command tree".
# They did not: 29 of ~98 screens were visited at all and only 20 got the
# ``\b`` check, so four real leaf commands shipped a literal ``\b`` to users
# for as long as the suite had been green -- `source search`,
# `source rebuild-search`, `graph package export` and `graph package load`,
# each of which used a raw-string docstring, so Python left ``\b`` as two
# characters and
# Click's paragraph escape never fired. The visible damage is the Examples
# block collapsing onto one run-on line.
#
# This test enumerates the tree itself rather than trusting a hand-list, so a
# newly-added command is covered the day it lands.
# ---------------------------------------------------------------------------


def _all_command_paths(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Every command path in the tree, root first, loading lazy groups.

    Mirrors ``_help``'s ``sys.argv`` trick: LazyGroup decides whether to
    import the real subcommands from ``sys.argv``, so the path being
    inspected has to be spelled there before ``list_commands`` is
    meaningful.
    """
    import click

    from chaoscypher_cli.__main__ import main

    def walk(path: tuple[str, ...]) -> list[tuple[str, ...]]:
        found = [path]
        monkeypatch.setattr(sys, "argv", ["chaoscypher", *path, "--help"])
        cmd: Any = main
        for token in path:
            cmd = cmd.get_command(click.Context(cmd), token)
            if cmd is None:
                return found
        names = (
            sorted(cmd.list_commands(click.Context(cmd))) if isinstance(cmd, click.Group) else []
        )
        for name in names:
            found.extend(walk((*path, name)))
        return found

    return walk(())


def test_no_help_screen_leaks_click_paragraph_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""No ``--help`` screen anywhere may print a literal ``\b``.

    A literal ``\b`` in rendered output means the docstring was malformed
    -- almost always a raw-string (``r``-prefixed) docstring, which stops
    Python turning ``\b`` into the backspace byte Click looks for. The user
    then sees raw backslash-b AND loses the paragraph-reflow suppression the
    token exists for, so the surrounding block renders as one run-on line.
    """
    paths = _all_command_paths(monkeypatch)
    assert len(paths) > 50, f"tree walk collapsed to {len(paths)} screens — enumeration is broken"

    offenders: list[str] = []
    for path in paths:
        result = _help(monkeypatch, *path)
        if "\\b" in result.output:
            offenders.append(" ".join(path) or "(root)")

    assert not offenders, (
        "These --help screens leak a literal '\\b' sequence. Fix the "
        "command docstring: drop its raw-string (r) prefix so Python emits "
        "the real backspace byte Click uses to suppress paragraph reflow.\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Every `chaoscypher config set <key>` hint names a real settings key.
#
# `source extract`'s no-LLM hint told users to run `config set llm.provider`,
# a key `LLMSettings` (``extra="forbid"``) does not have, so the one actionable
# line printed on that error path was itself a validation error. This walks
# every hint in the package source against the real `Settings` model.
# ---------------------------------------------------------------------------

_CONFIG_SET_HINT = re.compile(r"\bchaoscypher config set ([A-Za-z_][\w.]*)")


def _settings_key_exists(key: str) -> bool:
    from pydantic import BaseModel

    from chaoscypher_core.app_config import Settings

    model: Any = Settings
    for part in key.split("."):
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            return False
        field = model.model_fields.get(part)
        if field is None:
            return False
        model = field.annotation
    return True


def test_config_set_hints_name_real_settings_keys() -> None:
    import chaoscypher_cli

    src_root = Path(chaoscypher_cli.__file__).resolve().parent
    hints: list[tuple[str, str]] = []
    for py_file in sorted(src_root.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            for key in _CONFIG_SET_HINT.findall(line):
                hints.append((f"{py_file.relative_to(src_root)}:{lineno}", key))

    assert hints, "pattern found no `chaoscypher config set` hints — the scan is broken"
    offenders = [f"{where}: {key}" for where, key in hints if not _settings_key_exists(key)]
    assert not offenders, (
        "`chaoscypher config set` hints naming a key the settings schema "
        "rejects:\n" + "\n".join(offenders)
    )


_CLI_INDEX = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "docs"
    / "docs"
    / "reference"
    / "cli"
    / "index.md"
)


def test_cli_index_page_lists_every_top_level_command() -> None:
    """The CLI reference index names every top-level command with its registered help line.

    ``reference/cli/index.md`` hand-copies ``chaoscypher --help`` and a
    command table. ``memory`` shipped (2026-10-20) without either row, so a
    reader of the index could not find the command; this reads the registry
    instead of a reader.
    """
    from chaoscypher_cli.__main__ import LAZY_COMMANDS

    text = _CLI_INDEX.read_text(encoding="utf-8")
    assert sorted(LAZY_COMMANDS) == TOP_LEVEL_COMMANDS, "TOP_LEVEL_COMMANDS mirrors LAZY_COMMANDS"
    for name, (_target, help_line) in LAZY_COMMANDS.items():
        first_line = help_line.split("\n", 1)[0].strip()
        assert re.search(
            rf"^  {re.escape(name)}\s+{re.escape(first_line[:40])}", text, re.MULTILINE
        ), f"{name!r} ({first_line!r}) is missing from the --help block of {_CLI_INDEX.name}"
        assert re.search(rf"^\| \[?`{re.escape(name)}`", text, re.MULTILINE), (
            f"{name!r} has no row in the command tables of {_CLI_INDEX.name}"
        )
