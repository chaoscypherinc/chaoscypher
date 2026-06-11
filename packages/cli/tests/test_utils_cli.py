# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CLI utility helpers: llm_check, paths, console, display."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from rich.console import Console

from chaoscypher_core.models import SourceStatus


# ============================================================================
# utils/llm_check.py — is_llm_configured
# ============================================================================


class TestIsLlmConfigured:
    """is_llm_configured() reads engine config from settings.yaml (2026-06 unification)."""

    def test_false_when_nothing_configured(self, isolated_settings: Path) -> None:
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is False

    def test_true_for_ollama_in_settings_yaml(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text(
            yaml.safe_dump({"setup_completed": True, "llm": {"chat_provider": "ollama"}})
        )
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is True

    def test_cloud_provider_requires_key(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text(
            yaml.safe_dump({"setup_completed": True, "llm": {"chat_provider": "openai"}})
        )
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is False  # no openai_api_key anywhere

    def test_cloud_provider_with_key_in_file(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text(
            yaml.safe_dump(
                {
                    "setup_completed": True,
                    "llm": {"chat_provider": "openai", "openai_api_key": "sk-1"},
                }
            )
        )
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is True

    def test_anthropic_with_key_in_file(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text(
            yaml.safe_dump(
                {
                    "setup_completed": True,
                    "llm": {"chat_provider": "anthropic", "anthropic_api_key": "ant-1"},
                }
            )
        )
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is True

    def test_gemini_with_key_in_file(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text(
            yaml.safe_dump(
                {
                    "setup_completed": True,
                    "llm": {"chat_provider": "gemini", "gemini_api_key": "AIza-1"},
                }
            )
        )
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is True

    def test_unknown_provider_is_false(self, isolated_settings: Path) -> None:
        (isolated_settings / "settings.yaml").write_text(
            yaml.safe_dump({"setup_completed": True, "llm": {"chat_provider": "unknown_provider"}})
        )
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is False

    def test_env_provider_short_circuits(
        self, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CHAOSCYPHER_LLM_PROVIDER=ollama satisfies the check with no file."""
        monkeypatch.setenv("CHAOSCYPHER_LLM_PROVIDER", "ollama")
        from chaoscypher_cli.utils.llm_check import is_llm_configured

        assert is_llm_configured() is True


# ============================================================================
# utils/llm_check.py — check_llm_or_skip
# ============================================================================


def _check_llm_or_skip_with_prompt(
    prompt_return_value: str,
    is_configured_values: list[bool] | None = None,
    setup_raises: Exception | None = None,
) -> tuple[bool, bool]:
    """Call check_llm_or_skip() with Prompt patched at rich.prompt.Prompt.ask."""
    from chaoscypher_cli.utils.llm_check import check_llm_or_skip

    if is_configured_values is None:
        is_configured_values = [False]

    call_idx = [0]

    def side_effect() -> bool:
        val = is_configured_values[min(call_idx[0], len(is_configured_values) - 1)]
        call_idx[0] += 1
        return val

    fake_setup = MagicMock()
    if setup_raises is not None:
        fake_setup.setup.make_context.side_effect = setup_raises
    else:
        fake_setup.setup.make_context.return_value = MagicMock()

    with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", side_effect=side_effect):
        with patch("chaoscypher_cli.utils.llm_check.console"):
            with patch("rich.prompt.Prompt.ask", return_value=prompt_return_value):
                with patch.dict(
                    "sys.modules",
                    {"chaoscypher_cli.commands.setup": fake_setup},
                ):
                    return check_llm_or_skip("entity extraction")


class TestCheckLlmOrSkip:
    """Tests for check_llm_or_skip() decision matrix."""

    def test_proceed_no_skip_when_configured(self) -> None:
        """(True, False) when LLM is configured — no prompt shown."""
        from chaoscypher_cli.utils.llm_check import check_llm_or_skip

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", return_value=True):
            proceed, skip = check_llm_or_skip("entity extraction")

        assert proceed is True
        assert skip is False

    def test_cancel_when_user_picks_option_3(self) -> None:
        """(False, False) when user chooses Cancel."""
        proceed, skip = _check_llm_or_skip_with_prompt("3")
        assert proceed is False
        assert skip is False

    def test_skip_llm_when_user_picks_option_2(self) -> None:
        """(True, True) when user chooses Continue without LLM."""
        proceed, skip = _check_llm_or_skip_with_prompt("2")
        assert proceed is True
        assert skip is True

    def test_proceed_no_skip_after_setup_succeeds(self) -> None:
        """(True, False) when user picks option 1 and setup configures LLM."""
        proceed, skip = _check_llm_or_skip_with_prompt("1", is_configured_values=[False, True])
        assert proceed is True
        assert skip is False

    def test_proceed_with_skip_after_setup_still_not_configured(self) -> None:
        """(True, True) when user picks option 1 but LLM still not configured after setup."""
        # Both calls to is_llm_configured() return False
        proceed, skip = _check_llm_or_skip_with_prompt("1", is_configured_values=[False, False])
        assert proceed is True
        assert skip is True

    def test_false_false_when_setup_raises_in_option_1(self) -> None:
        """(False, False) when user picks option 1 but setup raises."""
        proceed, skip = _check_llm_or_skip_with_prompt("1", setup_raises=RuntimeError("boom"))
        assert proceed is False
        assert skip is False

    def test_prints_warning_and_options_when_not_configured(self) -> None:
        """Warning and option list should be printed when LLM is absent."""
        from chaoscypher_cli.utils.llm_check import check_llm_or_skip

        printed: list[str] = []

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", return_value=False):
            with patch("chaoscypher_cli.utils.llm_check.console") as mock_console:
                mock_console.print.side_effect = lambda msg: printed.append(str(msg))
                with patch("rich.prompt.Prompt.ask", return_value="3"):
                    check_llm_or_skip("graph analysis")

        combined = " ".join(printed)
        assert "Warning" in combined or "LLM not configured" in combined
        # Options should have been printed
        assert "1" in combined


# ============================================================================
# utils/paths.py
# ============================================================================


class TestGetConfigDir:
    """get_config_dir returns a Path and creates it."""

    def test_returns_path_instance(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_config_dir

        expected = tmp_path / "config" / "chaoscypher"

        with patch("chaoscypher_cli.utils.paths.user_config_dir", return_value=str(expected)):
            result = get_config_dir()

        assert isinstance(result, Path)
        assert result == expected
        assert result.exists()

    def test_creates_nested_directory(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_config_dir

        target = tmp_path / "deep" / "nested" / "chaoscypher"
        assert not target.exists()

        with patch("chaoscypher_cli.utils.paths.user_config_dir", return_value=str(target)):
            get_config_dir()

        assert target.exists()


class TestGetPackagesDir:
    """get_packages_dir resolves data_dir like engine_config and appends 'packages'."""

    def test_env_var_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from chaoscypher_cli.utils.paths import get_packages_dir

        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path / "data"))

        result = get_packages_dir()

        assert result == tmp_path / "data" / "packages"
        assert result.exists()  # mkdir parity with the historical implementation

    def test_matches_engine_config_data_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single packages-dir authority: identical resolution to engine_config."""
        from chaoscypher_cli import engine_config
        from chaoscypher_cli.utils.paths import get_packages_dir

        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path / "dd"))

        assert get_packages_dir() == engine_config.data_dir() / "packages"

    def test_windows_default_path_has_no_doubled_app_segment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        r"""Default resolution must not produce ``chaoscypher\chaoscypher``.

        The old implementation called platformdirs without ``appauthor=False``,
        which on Windows doubles the app segment
        (``%LOCALAPPDATA%\chaoscypher\chaoscypher``) — the split-brain that
        made ``lexicon list`` and ``lexicon remove`` scan different dirs.
        """
        import platformdirs

        from chaoscypher_cli.utils.paths import get_packages_dir

        monkeypatch.delenv("CHAOSCYPHER_DATA_DIR", raising=False)

        resolved: dict[str, str] = {}
        real_user_data_dir = platformdirs.user_data_dir

        def spy(*args: object, **kwargs: object) -> str:
            # Record what production would resolve, but sandbox the mkdir.
            resolved["data_dir"] = real_user_data_dir(*args, **kwargs)  # type: ignore[arg-type]
            return str(tmp_path / "sandbox")

        monkeypatch.setattr(platformdirs, "user_data_dir", spy)

        result = get_packages_dir()

        assert result == tmp_path / "sandbox" / "packages"
        assert Path(resolved["data_dir"]).parts.count("chaoscypher") == 1


# ============================================================================
# utils/console.py
# ============================================================================


class TestGetConsole:
    """get_console() returns a singleton Console."""

    def test_returns_console_instance(self) -> None:
        from chaoscypher_cli.utils.console import get_console

        result = get_console()
        assert isinstance(result, Console)

    def test_returns_same_instance_on_second_call(self) -> None:
        from chaoscypher_cli.utils import console as console_mod

        # Reset singleton to exercise the creation branch
        original = console_mod._console
        console_mod._console = None
        try:
            first = console_mod.get_console()
            second = console_mod.get_console()
            assert first is second
        finally:
            console_mod._console = original

    def test_singleton_set_after_first_call(self) -> None:
        from chaoscypher_cli.utils import console as console_mod

        original = console_mod._console
        console_mod._console = None
        try:
            c = console_mod.get_console()
            assert console_mod._console is c
        finally:
            console_mod._console = original


class TestPrintError:
    """print_error formats message with [red]Error:[/red] prefix."""

    def test_output_contains_message(self) -> None:
        from chaoscypher_cli.utils.console import print_error

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_error("something went wrong")

        assert "something went wrong" in buf.getvalue()

    def test_output_contains_error_label(self) -> None:
        from chaoscypher_cli.utils.console import print_error

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_error("disk full")

        assert "Error" in buf.getvalue()


class TestPrintSuccess:
    """print_success formats message with [green]Success:[/green] prefix."""

    def test_output_contains_message(self) -> None:
        from chaoscypher_cli.utils.console import print_success

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_success("operation complete")

        assert "operation complete" in buf.getvalue()

    def test_output_contains_success_label(self) -> None:
        from chaoscypher_cli.utils.console import print_success

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_success("done")

        assert "Success" in buf.getvalue()


class TestPrintJson:
    """print_json emits machine-safe output: verbatim, unwrapped, markup-proof."""

    def test_markup_like_substrings_survive_verbatim(self) -> None:
        """User data resembling Rich markup must not be swallowed as tags."""
        from chaoscypher_cli.utils.console import print_json

        payload = json.dumps({"name": "[bold]not-markup[/bold]"})
        buf = StringIO()
        test_console = Console(file=buf, width=80)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_json(payload)

        assert json.loads(buf.getvalue()) == {"name": "[bold]not-markup[/bold]"}

    def test_unbalanced_closing_tag_does_not_raise(self) -> None:
        """A dangling [/red]-style substring must not crash with MarkupError."""
        from chaoscypher_cli.utils.console import print_json

        payload = json.dumps({"name": "[/red] dangling close"})
        buf = StringIO()
        test_console = Console(file=buf, width=80)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_json(payload)  # must not raise

        assert json.loads(buf.getvalue()) == {"name": "[/red] dangling close"}

    def test_long_payload_not_wrapped(self) -> None:
        """Payloads wider than the console must stay on one parseable line."""
        from chaoscypher_cli.utils.console import print_json

        payload = json.dumps({"key": "x" * 300})
        buf = StringIO()
        test_console = Console(file=buf, width=40)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_json(payload)

        assert json.loads(buf.getvalue()) == {"key": "x" * 300}


# ============================================================================
# utils/display.py
# ============================================================================


class TestGetStatusColor:
    """get_status_color returns correct Rich color names."""

    def test_uploaded_is_yellow(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color("uploaded") == "yellow"

    def test_failed_is_red(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color("failed") == "red"

    def test_unknown_status_is_dim(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color("nonexistent_status") == "dim"

    def test_indexing_is_blue(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.INDEXING) == "blue"

    def test_indexed_is_cyan(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.INDEXED) == "cyan"

    def test_extracting_is_blue(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.EXTRACTING) == "blue"

    def test_extracted_is_green(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.EXTRACTED) == "green"

    def test_committing_is_blue(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.COMMITTING) == "blue"

    def test_committed_is_green(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.COMMITTED) == "green"

    def test_awaiting_confirmation_is_magenta(self) -> None:
        from chaoscypher_cli.utils.display import get_status_color

        assert get_status_color(SourceStatus.AWAITING_CONFIRMATION) == "magenta"


class TestGetQualityColor:
    """get_quality_color returns correct Rich color based on grade thresholds."""

    def test_grade_70_is_green(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(70) == "green"

    def test_grade_100_is_green(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(100) == "green"

    def test_grade_50_is_cyan(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(50) == "cyan"

    def test_grade_69_is_cyan(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(69) == "cyan"

    def test_grade_30_is_yellow(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(30) == "yellow"

    def test_grade_49_is_yellow(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(49) == "yellow"

    def test_grade_29_is_red(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(29) == "red"

    def test_grade_0_is_red(self) -> None:
        from chaoscypher_cli.utils.display import get_quality_color

        assert get_quality_color(0) == "red"
