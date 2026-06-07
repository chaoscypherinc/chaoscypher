# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI config commands."""

from collections.abc import Callable


class TestConfigCommands:
    """Test chaoscypher config show/get/path commands."""

    def test_config_path(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Config path command shows the config file location."""
        result = run_cli(["config", "path"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert len(result.output.strip()) > 0

    def test_config_show(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Config show command displays current configuration."""
        result = run_cli(["config", "show"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"

    def test_config_show_json(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Config show with JSON format returns valid output."""
        result = run_cli(["config", "show", "--format", "json"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
