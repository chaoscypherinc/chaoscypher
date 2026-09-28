# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for `chaoscypher benchmark list`."""

from __future__ import annotations

from click.testing import CliRunner

from chaoscypher_cli.commands.benchmark.list import list_cmd


def test_list_runs_without_error():
    """list_cmd discovers built-in datasets/configs and prints them.

    No assertion on exact content - this just confirms the command can
    enumerate the package-bundled defaults without crashing.
    """
    runner = CliRunner()
    result = runner.invoke(list_cmd, [])
    assert result.exit_code == 0, result.output
    # The bundled defaults include the canonical 'extraction' config and
    # the three v1 datasets - check at least one of each shows up.
    assert "extraction" in result.output
    assert "war_and_peace_tiny" in result.output


def test_list_names_the_real_command_and_shows_probe_domain():
    """The table title must name the command users can type, and the probes
    dataset declares a domain like every other kind (2026-09-24 llm audit).
    """
    runner = CliRunner()
    result = runner.invoke(list_cmd, [])
    assert result.exit_code == 0, result.output
    assert "chaoscypher benchmark run" in result.output
    assert "`bench run" not in result.output
    probe_rows = [line for line in result.output.splitlines() if "probes" in line]
    assert probe_rows, result.output
    assert any("literary" in line for line in probe_rows), probe_rows
