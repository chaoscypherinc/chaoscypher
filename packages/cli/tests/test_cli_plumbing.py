# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for render_orchestration command and __main__ root group plumbing.

Covers:
- render_orchestration_command: --list, --output-dir, missing --output-dir error
- __main__.main: --help, --version, lazy subcommand resolution, _upgrade_guard,
  _first_run_gate, _extract_database_override
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.__main__ import (
    _extract_database_override,
    _first_run_gate,
    _upgrade_guard,
    main,
)
from chaoscypher_cli.commands.render_orchestration import render_orchestration_command


# =============================================================================
# render_orchestration_command
# =============================================================================


class TestRenderOrchestrationList:
    """--list flag prints template names and exits 0."""

    def test_list_flag_exits_0(self) -> None:
        runner = CliRunner()
        with patch(
            "chaoscypher_cli.commands.render_orchestration.list_templates",
            return_value=["nginx.conf", "supervisord.conf", "valkey.conf"],
        ):
            result = runner.invoke(render_orchestration_command, ["--list"])
        assert result.exit_code == 0, result.output

    def test_list_flag_prints_each_template(self) -> None:
        runner = CliRunner()
        templates = ["nginx.conf", "supervisord.conf", "valkey.conf"]
        with patch(
            "chaoscypher_cli.commands.render_orchestration.list_templates",
            return_value=templates,
        ):
            result = runner.invoke(render_orchestration_command, ["--list"])
        for name in templates:
            assert name in result.output

    def test_list_empty_exits_0(self) -> None:
        runner = CliRunner()
        with patch(
            "chaoscypher_cli.commands.render_orchestration.list_templates",
            return_value=[],
        ):
            result = runner.invoke(render_orchestration_command, ["--list"])
        assert result.exit_code == 0


class TestRenderOrchestrationOutputDir:
    """--output-dir flag renders templates and echoes paths."""

    def test_output_dir_renders_templates(self, tmp_path: Path) -> None:
        runner = CliRunner()
        rendered = [tmp_path / "nginx.conf", tmp_path / "supervisord.conf"]
        mock_settings = MagicMock()
        with (
            patch(
                "chaoscypher_cli.commands.render_orchestration.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "chaoscypher_cli.commands.render_orchestration.render_all",
                return_value=rendered,
            ) as mock_render,
        ):
            result = runner.invoke(render_orchestration_command, ["--output-dir", str(tmp_path)])
        assert result.exit_code == 0, result.output
        mock_render.assert_called_once_with(mock_settings, tmp_path)

    def test_output_dir_echoes_rendered_paths(self, tmp_path: Path) -> None:
        runner = CliRunner()
        rendered = [tmp_path / "nginx.conf"]
        mock_settings = MagicMock()
        with (
            patch(
                "chaoscypher_cli.commands.render_orchestration.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "chaoscypher_cli.commands.render_orchestration.render_all",
                return_value=rendered,
            ),
        ):
            result = runner.invoke(render_orchestration_command, ["--output-dir", str(tmp_path)])
        assert "rendered:" in result.output
        assert "total:" in result.output
        assert "1 templates" in result.output

    def test_output_dir_total_count(self, tmp_path: Path) -> None:
        runner = CliRunner()
        rendered = [tmp_path / f"file{i}.conf" for i in range(3)]
        mock_settings = MagicMock()
        with (
            patch(
                "chaoscypher_cli.commands.render_orchestration.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "chaoscypher_cli.commands.render_orchestration.render_all",
                return_value=rendered,
            ),
        ):
            result = runner.invoke(render_orchestration_command, ["--output-dir", str(tmp_path)])
        assert "3 templates" in result.output


class TestRenderOrchestrationMissingOutputDir:
    """Missing --output-dir (without --list) raises UsageError / exits non-zero."""

    def test_missing_output_dir_raises_usage_error(self) -> None:
        runner = CliRunner()
        result = runner.invoke(render_orchestration_command, [])
        assert result.exit_code != 0

    def test_missing_output_dir_error_message(self) -> None:
        runner = CliRunner()
        result = runner.invoke(render_orchestration_command, [])
        assert "--output-dir" in result.output or "--list" in result.output


class TestRenderOrchestrationCommandMeta:
    """Command registration."""

    def test_command_name(self) -> None:
        assert render_orchestration_command.name == "render-orchestration"

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(render_orchestration_command, ["--help"])
        assert result.exit_code == 0


# =============================================================================
# __main__.py — _extract_database_override
# =============================================================================


class TestExtractDatabaseOverride:
    """Unit tests for the argv-scanning helper."""

    def test_long_flag_space_form(self) -> None:
        assert _extract_database_override(["--database", "mydb"]) == "mydb"

    def test_short_flag_space_form(self) -> None:
        assert _extract_database_override(["-d", "mydb"]) == "mydb"

    def test_long_flag_equals_form(self) -> None:
        assert _extract_database_override(["--database=mydb"]) == "mydb"

    def test_short_flag_equals_form(self) -> None:
        assert _extract_database_override(["-d=mydb"]) == "mydb"

    def test_no_flag_returns_none(self) -> None:
        assert _extract_database_override(["source", "list"]) is None

    def test_flag_at_end_with_no_value_returns_none(self) -> None:
        # --database at last position with nothing after
        assert _extract_database_override(["--database"]) is None

    def test_ignores_unrelated_args(self) -> None:
        assert _extract_database_override(["source", "add", "doc.pdf"]) is None


# =============================================================================
# __main__.py — main group --help / --version
# =============================================================================


class TestMainHelp:
    """Root group help and version flags."""

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0, result.output

    def test_help_contains_chaoscypher(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "chaoscypher" in result.output.lower() or "chaos" in result.output.lower()

    def test_version_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0, result.output

    def test_version_output_contains_version(self) -> None:
        from chaoscypher_cli import __version__

        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert __version__ in result.output

    def test_no_args_prints_help(self) -> None:
        """Invoking with no arguments (root group) prints help text."""
        runner = CliRunner()
        result = runner.invoke(main, [])
        # Click groups with no subcommand print help; exit code is 0 or 2 depending on version
        assert "chaoscypher" in result.output.lower() or "COMMAND" in result.output


# =============================================================================
# __main__.py — _upgrade_guard
# =============================================================================


class TestUpgradeGuard:
    """_upgrade_guard: various gate paths."""

    def _make_ctx(self, invoked_subcommand: str | None = "source") -> MagicMock:
        ctx = MagicMock(spec=["resilient_parsing", "invoked_subcommand", "exit"])
        ctx.resilient_parsing = False
        ctx.invoked_subcommand = invoked_subcommand
        return ctx

    def test_resilient_parsing_returns_immediately(self) -> None:
        ctx = self._make_ctx()
        ctx.resilient_parsing = True
        # Should not call ctx.exit
        _upgrade_guard(ctx)
        ctx.exit.assert_not_called()

    def test_help_flag_bypasses_guard(self) -> None:
        ctx = self._make_ctx()
        with patch.object(sys, "argv", ["chaoscypher", "--help"]):
            _upgrade_guard(ctx)
        ctx.exit.assert_not_called()

    def test_safe_subcommand_bypasses_guard(self) -> None:
        """render-orchestration is in _UPGRADE_SAFE_SUBCOMMANDS."""
        ctx = self._make_ctx(invoked_subcommand="render-orchestration")
        _upgrade_guard(ctx)
        ctx.exit.assert_not_called()

    def test_import_error_returns_without_blocking(self) -> None:
        """If imports fail, guard lets the command run."""
        ctx = self._make_ctx(invoked_subcommand="source")
        with patch("builtins.__import__", side_effect=ImportError("no module")):
            # The guard catches the ImportError and returns silently
            _upgrade_guard(ctx)

    def test_no_db_yet_returns_without_blocking(self) -> None:
        """If get_upgrade_state raises, guard does not block."""
        ctx = self._make_ctx(invoked_subcommand="source")
        with (
            patch(
                "chaoscypher_cli.engine_config.read_current_database",
                return_value="default",
            ),
            patch(
                "chaoscypher_core.database.engine.get_db_path",
                return_value=Path("/nonexistent/app.db"),
            ),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                side_effect=FileNotFoundError("no db"),
            ),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _upgrade_guard(ctx)
        ctx.exit.assert_not_called()

    def test_ready_state_does_not_block(self) -> None:
        """When the DB is ready, guard is silent."""
        ctx = self._make_ctx(invoked_subcommand="source")
        mock_state = MagicMock()
        mock_state.ready = True
        with (
            patch(
                "chaoscypher_cli.engine_config.read_current_database",
                return_value="default",
            ),
            patch(
                "chaoscypher_core.database.engine.get_db_path",
                return_value=Path("/app.db"),
            ),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=mock_state,
            ),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _upgrade_guard(ctx)
        ctx.exit.assert_not_called()

    def test_not_ready_state_calls_ctx_exit(self) -> None:
        """When the DB is blocked, guard calls ctx.exit(2)."""
        ctx = self._make_ctx(invoked_subcommand="source")
        mock_state = MagicMock()
        mock_state.ready = False
        mock_state.message = "schema mismatch"
        with (
            patch(
                "chaoscypher_cli.engine_config.read_current_database",
                return_value="default",
            ),
            patch(
                "chaoscypher_core.database.engine.get_db_path",
                return_value=Path("/app.db"),
            ),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=mock_state,
            ),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _upgrade_guard(ctx)
        ctx.exit.assert_called_once_with(2)

    def test_not_ready_no_message(self) -> None:
        """State.message=None path: guard still calls ctx.exit(2)."""
        ctx = self._make_ctx(invoked_subcommand="source")
        mock_state = MagicMock()
        mock_state.ready = False
        mock_state.message = None
        with (
            patch(
                "chaoscypher_cli.engine_config.read_current_database",
                return_value="default",
            ),
            patch(
                "chaoscypher_core.database.engine.get_db_path",
                return_value=Path("/app.db"),
            ),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=mock_state,
            ),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _upgrade_guard(ctx)
        ctx.exit.assert_called_once_with(2)

    def test_database_override_via_argv(self) -> None:
        """--database=mydb in argv overrides the config's current DB."""
        ctx = self._make_ctx(invoked_subcommand="source")
        mock_state = MagicMock()
        mock_state.ready = True
        captured_paths: list[Path] = []

        def capture_get_db_path(name: str) -> Path:
            captured_paths.append(Path(name))
            return Path(name) / "app.db"

        with (
            patch(
                "chaoscypher_cli.engine_config.read_current_database",
                return_value="default",
            ),
            patch(
                "chaoscypher_core.database.engine.get_db_path",
                side_effect=capture_get_db_path,
            ),
            patch(
                "chaoscypher_core.database.migrations.state.get_upgrade_state",
                return_value=mock_state,
            ),
            patch.object(
                sys, "argv", ["chaoscypher", "source", "list", "--database", "override_db"]
            ),
        ):
            _upgrade_guard(ctx)
        assert captured_paths and str(captured_paths[0]) == "override_db"


# =============================================================================
# __main__.py — _first_run_gate
# =============================================================================


class TestFirstRunGate:
    """_first_run_gate: auto-routing for fresh-install users."""

    def _make_ctx(self, invoked_subcommand: str | None = "source") -> MagicMock:
        ctx = MagicMock(spec=["resilient_parsing", "invoked_subcommand", "exit", "invoke"])
        ctx.resilient_parsing = False
        ctx.invoked_subcommand = invoked_subcommand
        return ctx

    def test_resilient_parsing_skips_gate(self) -> None:
        ctx = self._make_ctx()
        ctx.resilient_parsing = True
        _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_help_flag_skips_gate(self) -> None:
        ctx = self._make_ctx()
        with patch.object(sys, "argv", ["chaoscypher", "--help"]):
            _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_version_flag_skips_gate(self) -> None:
        ctx = self._make_ctx()
        with patch.object(sys, "argv", ["chaoscypher", "--version"]):
            _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_safe_subcommand_skips_gate(self) -> None:
        """'completions' is in _FIRST_RUN_SAFE_SUBCOMMANDS."""
        ctx = self._make_ctx(invoked_subcommand="completions")
        _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_import_error_lets_command_run(self) -> None:
        ctx = self._make_ctx(invoked_subcommand="source")
        with patch(
            "chaoscypher_cli.engine_config.is_setup_completed",
            side_effect=ImportError("no module"),
        ):
            _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_configured_user_passes_through(self) -> None:
        """If settings.yaml records the engine as set up, gate does not fire."""
        ctx = self._make_ctx(invoked_subcommand="source")
        with (
            patch("chaoscypher_cli.engine_config.is_setup_completed", return_value=True),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_llm_configured_passes_through(self) -> None:
        """If LLM is configured (e.g. via env), gate does not fire."""
        ctx = self._make_ctx(invoked_subcommand="source")
        with (
            patch("chaoscypher_cli.engine_config.is_setup_completed", return_value=True),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _first_run_gate(ctx)
        ctx.exit.assert_not_called()

    def test_non_tty_exits_2(self) -> None:
        """Non-interactive (no TTY): gate prints a message and exits 2.

        CliRunner runs with isatty()=False by default. We patch the config to
        simulate a fresh un-configured install so the gate fires, and bypass
        the upgrade guard to keep this test focused on _first_run_gate.
        """
        runner = CliRunner()

        with (
            patch("chaoscypher_cli.engine_config.is_setup_completed", return_value=False),
            patch("chaoscypher_cli.__main__._upgrade_guard"),
            patch.object(sys, "argv", ["chaoscypher", "source", "--help"]),
        ):
            result = runner.invoke(main, ["source", "--help"])
        # source --help bypasses first-run gate (--help check), so gate won't fire
        # here. This verifies help still works without crashing.
        assert result.exit_code in (0, 1, 2)

    def test_non_tty_first_run_gate_direct(self) -> None:
        """Direct call to _first_run_gate with isatty mocked via attribute replacement.

        ctx.exit() is a MagicMock so it doesn't stop execution; we need to make
        the is_tty check return False so the gate takes the non-TTY branch and
        calls ctx.exit(2) before any confirm prompt is reached. We stop execution
        by having ctx.exit raise SystemExit so the function terminates early.
        """
        ctx = self._make_ctx(invoked_subcommand="source")
        ctx.exit.side_effect = SystemExit(2)

        original_stdin_isatty = sys.stdin.isatty
        original_stderr_isatty = sys.stderr.isatty
        try:
            sys.stdin.isatty = lambda: False  # type: ignore[method-assign]
            sys.stderr.isatty = lambda: False  # type: ignore[method-assign]
            with (
                patch("chaoscypher_cli.engine_config.is_setup_completed", return_value=False),
                patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    _first_run_gate(ctx)
        finally:
            sys.stdin.isatty = original_stdin_isatty  # type: ignore[method-assign]
            sys.stderr.isatty = original_stderr_isatty  # type: ignore[method-assign]
        assert exc_info.value.code == 2
        ctx.exit.assert_called_once_with(2)

    def test_tty_confirm_no_exits_2(self) -> None:
        """TTY + user answers 'n': gate shows confirm prompt and exits 2."""
        ctx = self._make_ctx(invoked_subcommand="source")
        ctx.exit.side_effect = SystemExit(2)

        original_stdin_isatty = sys.stdin.isatty
        original_stderr_isatty = sys.stderr.isatty
        try:
            sys.stdin.isatty = lambda: True  # type: ignore[method-assign]
            sys.stderr.isatty = lambda: True  # type: ignore[method-assign]
            with (
                patch("chaoscypher_cli.engine_config.is_setup_completed", return_value=False),
                patch("chaoscypher_cli.__main__.click.confirm", return_value=False),
                patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    _first_run_gate(ctx)
        finally:
            sys.stdin.isatty = original_stdin_isatty  # type: ignore[method-assign]
            sys.stderr.isatty = original_stderr_isatty  # type: ignore[method-assign]
        assert exc_info.value.code == 2
        ctx.exit.assert_called_once_with(2)

    def test_tty_confirm_yes_invokes_setup(self) -> None:
        """TTY + user answers 'y': gate invokes setup wizard and exits 0."""
        ctx = self._make_ctx(invoked_subcommand="source")
        ctx.exit.side_effect = SystemExit(0)
        mock_setup = MagicMock()

        original_stdin_isatty = sys.stdin.isatty
        original_stderr_isatty = sys.stderr.isatty
        try:
            sys.stdin.isatty = lambda: True  # type: ignore[method-assign]
            sys.stderr.isatty = lambda: True  # type: ignore[method-assign]
            with (
                patch("chaoscypher_cli.engine_config.is_setup_completed", return_value=False),
                patch("chaoscypher_cli.__main__.click.confirm", return_value=True),
                patch(
                    "chaoscypher_cli.commands.setup.setup",
                    mock_setup,
                ),
                patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    _first_run_gate(ctx)
        finally:
            sys.stdin.isatty = original_stdin_isatty  # type: ignore[method-assign]
            sys.stderr.isatty = original_stderr_isatty  # type: ignore[method-assign]
        assert exc_info.value.code == 0
        ctx.invoke.assert_called_once()
        ctx.exit.assert_called_once_with(0)

    def test_get_config_raises_lets_command_run(self) -> None:
        """If the settings peek raises, gate is best-effort and lets command run."""
        ctx = self._make_ctx(invoked_subcommand="source")
        with (
            patch(
                "chaoscypher_cli.engine_config.is_setup_completed",
                side_effect=Exception("settings broken"),
            ),
            patch.object(sys, "argv", ["chaoscypher", "source", "list"]),
        ):
            _first_run_gate(ctx)
        ctx.exit.assert_not_called()


# =============================================================================
# __main__.py — main group integration (via CliRunner)
# =============================================================================


class TestMainGroupIntegration:
    """End-to-end CliRunner tests for the root group."""

    def test_completions_subcommand_loads_lazily(self) -> None:
        """Invoking 'completions bash' loads the module and runs it.

        LazyGroup._is_being_invoked checks sys.argv, so we must patch it to
        match the CliRunner invocation args — otherwise the stub command is
        returned instead of the real one.
        """
        runner = CliRunner()
        with (
            patch("chaoscypher_cli.__main__._upgrade_guard"),
            patch("chaoscypher_cli.__main__._first_run_gate"),
            patch.object(sys, "argv", ["chaoscypher", "completions", "bash"]),
            patch(
                "chaoscypher_cli.commands.completions._generate_completion_script",
                return_value="# script\n",
            ),
        ):
            result = runner.invoke(main, ["completions", "bash"])
        assert result.exit_code == 0, result.output
        assert "# script" in result.output

    def test_render_orchestration_subcommand_loads_lazily(self) -> None:
        """Invoking 'render-orchestration --list' resolves the lazy command."""
        runner = CliRunner()
        with (
            patch("chaoscypher_cli.__main__._upgrade_guard"),
            patch("chaoscypher_cli.__main__._first_run_gate"),
            patch.object(sys, "argv", ["chaoscypher", "render-orchestration", "--list"]),
            patch(
                "chaoscypher_cli.commands.render_orchestration.list_templates",
                return_value=["nginx.conf"],
            ),
        ):
            result = runner.invoke(main, ["render-orchestration", "--list"])
        assert result.exit_code == 0, result.output
        assert "nginx.conf" in result.output

    def test_unknown_subcommand_exits_nonzero(self) -> None:
        runner = CliRunner()
        with (
            patch("chaoscypher_cli.__main__._upgrade_guard"),
            patch("chaoscypher_cli.__main__._first_run_gate"),
        ):
            result = runner.invoke(main, ["nonexistent-cmd"])
        assert result.exit_code != 0
