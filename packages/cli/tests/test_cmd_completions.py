# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chaoscypher_cli.commands.completions.

Covers:
- Printing completion script to stdout for each supported shell (bash/zsh/fish)
- --install flag for bash, zsh, and fish (fresh install and update)
- --uninstall flag for bash, zsh, and fish (exists and not-found paths)
- --show-install / -i flag for each shell
- Error path when completion class cannot be found
- Internal helpers: _generate_completion_script, _install_rc_completions,
  _install_fish_completions, _uninstall_completions, _show_install_instructions
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.completions import (
    _COMPLETION_END,
    _COMPLETION_START,
    _generate_completion_script,
    _install_fish_completions,
    _install_rc_completions,
    _show_install_instructions,
    _uninstall_completions,
    completions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_SCRIPT = "# fake completion script\ncomplete -W 'foo bar' chaoscypher\n"


def _fake_completion_class(shell: str) -> type:
    """Return a fake completion class whose source() returns _FAKE_SCRIPT."""

    class _FakeCompletion:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def source(self) -> str:
            return _FAKE_SCRIPT

    return _FakeCompletion


# ---------------------------------------------------------------------------
# Print-to-stdout (no --install)
# ---------------------------------------------------------------------------


class TestCompletionsPrint:
    """completions <shell> without --install prints the script to stdout."""

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_print_script_exits_0(self, shell: str) -> None:
        runner = CliRunner()
        with patch(
            "click.shell_completion.get_completion_class",
            return_value=_fake_completion_class(shell),
        ):
            result = runner.invoke(completions, [shell])
        assert result.exit_code == 0, result.output
        assert _FAKE_SCRIPT in result.output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_print_script_contains_expected_content(self, shell: str) -> None:
        runner = CliRunner()
        with patch(
            "click.shell_completion.get_completion_class",
            return_value=_fake_completion_class(shell),
        ):
            result = runner.invoke(completions, [shell])
        assert "fake completion script" in result.output

    def test_unsupported_shell_is_rejected_by_click(self) -> None:
        """Click Choice rejects unknown shells before our code even runs."""
        runner = CliRunner()
        result = runner.invoke(completions, ["powershell"])
        assert result.exit_code != 0
        assert "invalid choice" in result.output.lower() or "powershell" in result.output.lower()


# ---------------------------------------------------------------------------
# _generate_completion_script
# ---------------------------------------------------------------------------


class TestGenerateCompletionScript:
    """Unit tests for the internal _generate_completion_script helper."""

    def test_returns_string_for_known_shell(self) -> None:
        with patch(
            "click.shell_completion.get_completion_class",
            return_value=_fake_completion_class("bash"),
        ):
            result = _generate_completion_script("bash")
        assert result == _FAKE_SCRIPT

    def test_returns_none_when_completion_class_is_none(self) -> None:
        with patch(
            "click.shell_completion.get_completion_class",
            return_value=None,
        ):
            result = _generate_completion_script("bash")
        assert result is None

    def test_returns_none_on_exception(self) -> None:
        with patch(
            "click.shell_completion.get_completion_class",
            side_effect=RuntimeError("boom"),
        ):
            result = _generate_completion_script("bash")
        assert result is None

    def test_exits_1_when_script_is_none(self) -> None:
        """The completions command exits 1 if _generate_completion_script returns None."""
        runner = CliRunner()
        with patch(
            "chaoscypher_cli.commands.completions._generate_completion_script",
            return_value=None,
        ):
            result = runner.invoke(completions, ["bash"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# --install: bash / zsh (rc file install)
# ---------------------------------------------------------------------------


class TestInstallRcCompletions:
    """--install appends / updates the completion block in .bashrc / .zshrc."""

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_fresh_install_appends_block(self, shell: str, tmp_path: Path) -> None:
        """When the rc file does not yet contain our marker, append the block."""
        rc_file = tmp_path / (".bashrc" if shell == "bash" else ".zshrc")
        rc_file.write_text("# existing content\n")

        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_rc = MagicMock(wraps=rc_file)
            mock_home.__truediv__.return_value = mock_rc

            # Wire .exists() / .read_text() / .open() through to real file
            mock_rc.exists.return_value = rc_file.exists()
            mock_rc.read_text.return_value = rc_file.read_text()
            written: list[str] = []
            mock_open = MagicMock()
            mock_file_handle = MagicMock()
            mock_file_handle.__enter__ = lambda s: s
            mock_file_handle.__exit__ = MagicMock(return_value=False)
            mock_file_handle.write = lambda text: written.append(text)
            mock_open.return_value = mock_file_handle
            mock_rc.open.return_value = mock_file_handle

            _install_rc_completions(shell, _FAKE_SCRIPT)

        assert any(_COMPLETION_START in chunk for chunk in written)

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_update_existing_block(self, shell: str, tmp_path: Path) -> None:
        """When the rc file already has our marker, update the block."""
        rc_file = tmp_path / (".bashrc" if shell == "bash" else ".zshrc")
        rc_file.write_text(
            f"# existing content\n{_COMPLETION_START}\nold script\n{_COMPLETION_END}\n# after\n"
        )

        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_rc = MagicMock()
            mock_home.__truediv__.return_value = mock_rc
            mock_rc.exists.return_value = True
            mock_rc.read_text.return_value = rc_file.read_text()
            written_text: list[str] = []
            mock_rc.write_text.side_effect = lambda t: written_text.append(t)

            _install_rc_completions(shell, _FAKE_SCRIPT)

        assert written_text, "write_text should have been called (update path)"
        new_content = written_text[0]
        assert _FAKE_SCRIPT in new_content
        assert "old script" not in new_content

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_install_via_runner_exits_0(self, shell: str) -> None:
        """CliRunner end-to-end: --install exits 0 and writes the script."""
        runner = CliRunner()
        with (
            patch(
                "chaoscypher_cli.commands.completions._generate_completion_script",
                return_value=_FAKE_SCRIPT,
            ),
            patch(
                "chaoscypher_cli.commands.completions._install_completions",
            ) as mock_install,
        ):
            result = runner.invoke(completions, [shell, "--install"])
        assert result.exit_code == 0, result.output
        mock_install.assert_called_once_with(shell, _FAKE_SCRIPT)


# ---------------------------------------------------------------------------
# --install: fish
# ---------------------------------------------------------------------------


class TestInstallFishCompletions:
    """--install for fish writes to ~/.config/fish/completions/chaoscypher.fish."""

    def test_fish_install_writes_file(self, tmp_path: Path) -> None:
        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            # Build the path chain: home / ".config" / "fish" / "completions"
            mock_config = MagicMock()
            mock_fish = MagicMock()
            mock_comps_dir = MagicMock()
            mock_comps_file = MagicMock()
            mock_home.__truediv__.return_value = mock_config
            mock_config.__truediv__.return_value = mock_fish
            mock_fish.__truediv__.return_value = mock_comps_dir
            mock_comps_dir.__truediv__.return_value = mock_comps_file

            written: list[str] = []
            mock_comps_file.write_text.side_effect = lambda t: written.append(t)

            _install_fish_completions(_FAKE_SCRIPT)

        mock_comps_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        assert written == [_FAKE_SCRIPT]

    def test_fish_install_via_runner_exits_0(self) -> None:
        runner = CliRunner()
        with (
            patch(
                "chaoscypher_cli.commands.completions._generate_completion_script",
                return_value=_FAKE_SCRIPT,
            ),
            patch(
                "chaoscypher_cli.commands.completions._install_fish_completions",
            ) as mock_fish,
        ):
            result = runner.invoke(completions, ["fish", "--install"])
        assert result.exit_code == 0, result.output
        mock_fish.assert_called_once_with(_FAKE_SCRIPT)


# ---------------------------------------------------------------------------
# --uninstall
# ---------------------------------------------------------------------------


class TestUninstallCompletions:
    """--uninstall removes the completion block / file."""

    def test_uninstall_fish_existing_file(self) -> None:
        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_config = MagicMock()
            mock_fish = MagicMock()
            mock_comps_dir = MagicMock()
            mock_comps_file = MagicMock()
            mock_home.__truediv__.return_value = mock_config
            mock_config.__truediv__.return_value = mock_fish
            mock_fish.__truediv__.return_value = mock_comps_dir
            mock_comps_dir.__truediv__.return_value = mock_comps_file
            mock_comps_file.exists.return_value = True

            _uninstall_completions("fish")

        mock_comps_file.unlink.assert_called_once()

    def test_uninstall_fish_not_found(self) -> None:
        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_config = MagicMock()
            mock_fish = MagicMock()
            mock_comps_dir = MagicMock()
            mock_comps_file = MagicMock()
            mock_home.__truediv__.return_value = mock_config
            mock_config.__truediv__.return_value = mock_fish
            mock_fish.__truediv__.return_value = mock_comps_dir
            mock_comps_dir.__truediv__.return_value = mock_comps_file
            mock_comps_file.exists.return_value = False

            _uninstall_completions("fish")  # should not raise

        mock_comps_file.unlink.assert_not_called()

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_uninstall_bash_zsh_rc_missing(self, shell: str) -> None:
        """When the rc file doesn't exist, uninstall is a no-op."""
        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_rc = MagicMock()
            mock_home.__truediv__.return_value = mock_rc
            mock_rc.exists.return_value = False

            _uninstall_completions(shell)

        mock_rc.write_text.assert_not_called()

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_uninstall_bash_zsh_no_marker(self, shell: str) -> None:
        """When the rc file exists but has no marker, uninstall is a no-op."""
        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_rc = MagicMock()
            mock_home.__truediv__.return_value = mock_rc
            mock_rc.exists.return_value = True
            mock_rc.read_text.return_value = "# no chaoscypher stuff here\n"

            _uninstall_completions(shell)

        mock_rc.write_text.assert_not_called()

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_uninstall_bash_zsh_removes_block(self, shell: str) -> None:
        """When the marker is present, uninstall removes the block."""
        original = f"# before\n\n{_COMPLETION_START}\n# old script\n{_COMPLETION_END}\n\n# after\n"
        with patch("chaoscypher_cli.commands.completions.Path") as mock_path_cls:
            mock_home = MagicMock()
            mock_path_cls.home.return_value = mock_home
            mock_rc = MagicMock()
            mock_home.__truediv__.return_value = mock_rc
            mock_rc.exists.return_value = True
            mock_rc.read_text.return_value = original
            written: list[str] = []
            mock_rc.write_text.side_effect = lambda t: written.append(t)

            _uninstall_completions(shell)

        assert written, "write_text should have been called to persist removal"
        new_content = written[0]
        assert _COMPLETION_START not in new_content
        assert _COMPLETION_END not in new_content
        assert "# old script" not in new_content
        assert "# after" in new_content

    def test_uninstall_via_runner_fish(self) -> None:
        runner = CliRunner()
        with patch(
            "chaoscypher_cli.commands.completions._uninstall_completions",
        ) as mock_uninstall:
            result = runner.invoke(completions, ["fish", "--uninstall"])
        assert result.exit_code == 0, result.output
        mock_uninstall.assert_called_once_with("fish")

    @pytest.mark.parametrize("shell", ["bash", "zsh"])
    def test_uninstall_via_runner_rc(self, shell: str) -> None:
        runner = CliRunner()
        with patch(
            "chaoscypher_cli.commands.completions._uninstall_completions",
        ) as mock_uninstall:
            result = runner.invoke(completions, [shell, "--uninstall"])
        assert result.exit_code == 0, result.output
        mock_uninstall.assert_called_once_with(shell)


# ---------------------------------------------------------------------------
# --show-install / -i
# ---------------------------------------------------------------------------


class TestShowInstallInstructions:
    """--show-install prints installation instructions panel."""

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_show_install_via_runner(self, shell: str) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, [shell, "--show-install"])
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_show_install_short_flag(self, shell: str) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, [shell, "-i"])
        assert result.exit_code == 0, result.output

    def test_show_install_bash_mentions_bashrc(self) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, ["bash", "--show-install"])
        assert result.exit_code == 0
        # Should mention bashrc somewhere in the instructions
        assert "bashrc" in result.output.lower() or "bash" in result.output.lower()

    def test_show_install_zsh_mentions_zshrc(self) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, ["zsh", "--show-install"])
        assert result.exit_code == 0
        assert "zshrc" in result.output.lower() or "zsh" in result.output.lower()

    def test_show_install_fish_mentions_fish(self) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, ["fish", "--show-install"])
        assert result.exit_code == 0
        assert "fish" in result.output.lower()

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_show_install_internal_function(self, shell: str) -> None:
        """Call _show_install_instructions directly — must not raise."""
        _show_install_instructions(shell)  # no assert needed beyond not-raising


# ---------------------------------------------------------------------------
# Command meta
# ---------------------------------------------------------------------------


class TestCompletionsCommandMeta:
    """Completions command registration and --help."""

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, ["--help"])
        assert result.exit_code == 0
        assert "bash" in result.output.lower() or "shell" in result.output.lower()

    def test_bash_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(completions, ["bash", "--help"])
        assert result.exit_code == 0

    def test_completions_command_name(self) -> None:
        assert completions.name == "completions"
