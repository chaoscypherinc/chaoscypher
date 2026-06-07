# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for basic CLI commands (version, help, completions, health)."""

from collections.abc import Callable


class TestBasicCommands:
    """Test version, help, and other read-only commands."""

    def test_version(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """--version flag returns version info."""
        result = run_cli(["--version"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert len(result.output.strip()) > 0

    def test_help(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """--help flag returns usage information."""
        result = run_cli(["--help"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert (
            "Chaos Cypher" in result.output
            or "ChaosCypher" in result.output
            or "chaoscypher" in result.output
        )
        assert "Commands:" in result.output

    def test_completions_bash(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Generating bash completions succeeds."""
        result = run_cli(["completions", "bash"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        # Bash completion output should contain _chaoscypher function or similar
        assert len(result.output.strip()) > 0

    def test_db_help(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Db --help lists all db subcommands."""
        result = run_cli(["db", "--help"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "create" in result.output
        assert "list" in result.output

    def test_graph_help(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Graph --help lists all graph subcommands."""
        result = run_cli(["graph", "--help"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "node" in result.output or "template" in result.output

    def test_source_help(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Source --help lists all source subcommands."""
        result = run_cli(["source", "--help"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "add" in result.output
