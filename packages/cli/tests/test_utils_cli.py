# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for CLI utility helpers: llm_check, paths, files, console, display."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
# utils/llm_check.py — require_llm_configured
# ============================================================================


class TestRequireLlmConfigured:
    """Tests for require_llm_configured()."""

    def test_returns_true_immediately_when_llm_configured(self) -> None:
        from chaoscypher_cli.utils.llm_check import require_llm_configured

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", return_value=True):
            result = require_llm_configured("test operation")

        assert result is True

    def test_returns_false_when_user_declines_setup(self) -> None:
        from chaoscypher_cli.utils.llm_check import require_llm_configured

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", return_value=False):
            with patch("chaoscypher_cli.utils.llm_check.Confirm.ask", return_value=False):
                with patch("chaoscypher_cli.utils.llm_check.console") as mock_console:
                    result = require_llm_configured("entity extraction")

        assert result is False
        mock_console.print.assert_called()

    def test_prints_operation_name_in_warning(self) -> None:
        from chaoscypher_cli.utils.llm_check import require_llm_configured

        captured_prints: list[str] = []

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", return_value=False):
            with patch("chaoscypher_cli.utils.llm_check.Confirm.ask", return_value=False):
                with patch("chaoscypher_cli.utils.llm_check.console") as mock_console:
                    mock_console.print.side_effect = lambda msg: captured_prints.append(str(msg))
                    require_llm_configured("graph search")

        combined = " ".join(captured_prints)
        # The operation name is capitalized inside the function
        assert "graph search" in combined or "Graph search" in combined

    def test_returns_false_when_setup_raises(self) -> None:
        from chaoscypher_cli.utils.llm_check import require_llm_configured

        fake_setup = MagicMock()
        fake_setup.setup.make_context.side_effect = RuntimeError("setup broken")

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", return_value=False):
            with patch("chaoscypher_cli.utils.llm_check.Confirm.ask", return_value=True):
                with patch("chaoscypher_cli.utils.llm_check.console"):
                    with patch.dict(
                        "sys.modules",
                        {"chaoscypher_cli.commands.setup": fake_setup},
                    ):
                        result = require_llm_configured("extraction")

        assert result is False

    def test_returns_true_after_successful_setup(self) -> None:
        """If user accepts setup and LLM becomes configured, returns True."""
        from chaoscypher_cli.utils.llm_check import require_llm_configured

        # First call (before setup) returns False; second call (after setup) returns True.
        side_effects = [False, True]
        call_count = [0]

        def side_effect() -> bool:
            val = side_effects[min(call_count[0], len(side_effects) - 1)]
            call_count[0] += 1
            return val

        fake_setup = MagicMock()
        fake_setup.setup.make_context.return_value = MagicMock()

        with patch("chaoscypher_cli.utils.llm_check.is_llm_configured", side_effect=side_effect):
            with patch("chaoscypher_cli.utils.llm_check.Confirm.ask", return_value=True):
                with patch("chaoscypher_cli.utils.llm_check.console"):
                    with patch.dict(
                        "sys.modules",
                        {"chaoscypher_cli.commands.setup": fake_setup},
                    ):
                        result = require_llm_configured("test op")

        assert result is True


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


class TestGetCacheDir:
    """get_cache_dir returns a Path and creates it."""

    def test_returns_path_instance(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_cache_dir

        expected = tmp_path / "cache" / "chaoscypher"

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(expected)):
            result = get_cache_dir()

        assert isinstance(result, Path)
        assert result.exists()

    def test_creates_directory(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_cache_dir

        target = tmp_path / "cache_new"

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(target)):
            result = get_cache_dir()

        assert result.exists()
        assert result == target


class TestGetDataDir:
    """get_data_dir returns a Path and creates it."""

    def test_returns_existing_path(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_data_dir

        target = tmp_path / "data"

        with patch("chaoscypher_cli.utils.paths.user_data_dir", return_value=str(target)):
            result = get_data_dir()

        assert isinstance(result, Path)
        assert result.exists()
        assert result == target


class TestGetPackagesDir:
    """get_packages_dir is data_dir / 'packages' and is created."""

    def test_returns_packages_subdir(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_packages_dir

        base = tmp_path / "data"

        with patch("chaoscypher_cli.utils.paths.user_data_dir", return_value=str(base)):
            result = get_packages_dir()

        assert result == base / "packages"
        assert result.exists()


class TestGetDatabasesDir:
    """get_databases_dir is data_dir / 'databases' and is created."""

    def test_returns_databases_subdir(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_databases_dir

        base = tmp_path / "data"

        with patch("chaoscypher_cli.utils.paths.user_data_dir", return_value=str(base)):
            result = get_databases_dir()

        assert result == base / "databases"
        assert result.exists()


class TestGetPackageCacheDir:
    """get_package_cache_dir handles name/version variants."""

    def test_simple_name_no_version(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_package_cache_dir

        base_cache = tmp_path / "cache"

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(base_cache)):
            result = get_package_cache_dir("mypkg")

        assert result == base_cache / "packages" / "mypkg"
        assert result.exists()

    def test_namespaced_name_splits_on_slash(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_package_cache_dir

        base_cache = tmp_path / "cache"

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(base_cache)):
            result = get_package_cache_dir("john/medical-ontology")

        assert result == base_cache / "packages" / "john" / "medical-ontology"
        assert result.exists()

    def test_version_appended_when_given(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_package_cache_dir

        base_cache = tmp_path / "cache"

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(base_cache)):
            result = get_package_cache_dir("john/medical-ontology", version="1.2.3")

        assert result == base_cache / "packages" / "john" / "medical-ontology" / "1.2.3"
        assert result.exists()

    def test_version_none_omits_version_segment(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_package_cache_dir

        base_cache = tmp_path / "cache"

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(base_cache)):
            result = get_package_cache_dir("mypkg", version=None)

        assert result == base_cache / "packages" / "mypkg"

    def test_creates_directory(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.paths import get_package_cache_dir

        base_cache = tmp_path / "cache"
        assert not (base_cache / "packages" / "newpkg").exists()

        with patch("chaoscypher_cli.utils.paths.user_cache_dir", return_value=str(base_cache)):
            result = get_package_cache_dir("newpkg")

        assert result.exists()


# ============================================================================
# utils/files.py
# ============================================================================


def _make_async_client_mock(
    file_content: bytes,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build an httpx.AsyncClient async context manager mock that streams content."""

    async def aiter_bytes(chunk_size: int = 1024):  # type: ignore[no-untyped-def]
        yield file_content

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {"content-length": str(len(file_content))}
    mock_response.aiter_bytes = aiter_bytes

    # response is also an async context manager
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_response
    # AsyncClient itself is an async context manager
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    return mock_client


class TestDownloadFile:
    """Tests for download_file() — network mocked via httpx."""

    @pytest.mark.asyncio
    async def test_creates_dest_and_returns_path(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.files import download_file

        dest = tmp_path / "subdir" / "file.ccx"
        file_content = b"hello archive"

        mock_client = _make_async_client_mock(file_content)
        mock_settings = MagicMock()
        mock_settings.cli.download_chunk_size_bytes = 1024

        with patch("chaoscypher_cli.utils.files.get_settings", return_value=mock_settings):
            with patch("chaoscypher_cli.utils.files.httpx.AsyncClient", return_value=mock_client):
                result = await download_file("https://example.com/file.ccx", dest, progress=False)

        assert result == dest
        assert dest.exists()
        assert dest.read_bytes() == file_content

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, tmp_path: Path) -> None:
        import httpx

        from chaoscypher_cli.utils.files import download_file

        dest = tmp_path / "file.ccx"
        mock_settings = MagicMock()
        mock_settings.cli.download_chunk_size_bytes = 1024

        # Client itself raises RequestError when used as async context manager
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=httpx.RequestError("no route"))
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("chaoscypher_cli.utils.files.get_settings", return_value=mock_settings):
            with patch("chaoscypher_cli.utils.files.httpx.AsyncClient", return_value=mock_client):
                with pytest.raises(httpx.RequestError):
                    await download_file("https://bad.example.com/pkg.ccx", dest, progress=False)

    @pytest.mark.asyncio
    async def test_download_with_auth_headers(self, tmp_path: Path) -> None:
        from chaoscypher_cli.utils.files import download_file

        dest = tmp_path / "auth_file.ccx"
        file_content = b"authenticated content"
        mock_settings = MagicMock()
        mock_settings.cli.download_chunk_size_bytes = 512

        captured_kwargs: list[dict] = []

        async def aiter_bytes(chunk_size: int = 512):  # type: ignore[no-untyped-def]
            yield file_content

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.headers = {"content-length": str(len(file_content))}
        mock_response.aiter_bytes = aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        def capture_stream(method: str, url: str, headers: dict) -> MagicMock:
            captured_kwargs.append({"headers": headers})
            return mock_response

        mock_client = MagicMock()
        mock_client.stream.side_effect = capture_stream
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("chaoscypher_cli.utils.files.get_settings", return_value=mock_settings):
            with patch("chaoscypher_cli.utils.files.httpx.AsyncClient", return_value=mock_client):
                await download_file(
                    "https://example.com/file.ccx",
                    dest,
                    progress=False,
                    headers={"Authorization": "Bearer token123"},
                )

        assert captured_kwargs[0]["headers"].get("Authorization") == "Bearer token123"

    @pytest.mark.asyncio
    async def test_download_with_progress_branch(self, tmp_path: Path) -> None:
        """Exercises the progress=True + content-length>0 branch (lines 94-103)."""
        from chaoscypher_cli.utils.files import download_file

        dest = tmp_path / "progress_file.ccx"
        file_content = b"binary data"
        mock_settings = MagicMock()
        mock_settings.cli.download_chunk_size_bytes = 64

        mock_client = _make_async_client_mock(file_content)
        # Rich Progress renders to a real console; mock it out to keep test fast
        with patch("chaoscypher_cli.utils.files.get_settings", return_value=mock_settings):
            with patch("chaoscypher_cli.utils.files.httpx.AsyncClient", return_value=mock_client):
                with patch("chaoscypher_cli.utils.files.Progress") as mock_progress_cls:
                    mock_prog = MagicMock()
                    mock_prog.__enter__ = MagicMock(return_value=mock_prog)
                    mock_prog.__exit__ = MagicMock(return_value=False)
                    mock_prog.add_task.return_value = 0
                    mock_progress_cls.return_value = mock_prog

                    result = await download_file(
                        "https://example.com/progress.ccx",
                        dest,
                        progress=True,  # trigger the progress branch
                    )

        assert result == dest
        # Progress.add_task should have been called with the filename
        mock_prog.add_task.assert_called_once()
        call_args = mock_prog.add_task.call_args[0][0]
        assert "progress_file.ccx" in call_args


class TestFilesReExports:
    """Verify the re-exported symbols from chaoscypher_core are present."""

    def test_ccx_extension_is_dot_ccx(self) -> None:
        from chaoscypher_cli.utils.files import CCX_EXTENSION

        assert CCX_EXTENSION == ".ccx"

    def test_create_archive_is_callable(self) -> None:
        from chaoscypher_cli.utils.files import create_archive

        assert callable(create_archive)

    def test_extract_archive_is_callable(self) -> None:
        from chaoscypher_cli.utils.files import extract_archive

        assert callable(extract_archive)

    def test_format_size_is_callable(self) -> None:
        from chaoscypher_cli.utils.files import format_size

        assert callable(format_size)

    def test_get_archive_info_is_callable(self) -> None:
        from chaoscypher_cli.utils.files import get_archive_info

        assert callable(get_archive_info)


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


class TestPrintWarning:
    """print_warning formats message with [yellow]Warning:[/yellow] prefix."""

    def test_output_contains_message(self) -> None:
        from chaoscypher_cli.utils.console import print_warning

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_warning("low disk space")

        assert "low disk space" in buf.getvalue()

    def test_output_contains_warning_label(self) -> None:
        from chaoscypher_cli.utils.console import print_warning

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_warning("check logs")

        assert "Warning" in buf.getvalue()


class TestPrintTable:
    """print_table renders headers and rows."""

    def test_headers_appear_in_output(self) -> None:
        from chaoscypher_cli.utils.console import print_table

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_table(["Name", "Version"], [["mypkg", "1.0.0"]])

        output = buf.getvalue()
        assert "Name" in output
        assert "Version" in output

    def test_row_values_appear_in_output(self) -> None:
        from chaoscypher_cli.utils.console import print_table

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_table(["Package", "Tag"], [["alpha", "stable"], ["beta", "dev"]])

        output = buf.getvalue()
        assert "alpha" in output
        assert "stable" in output

    def test_empty_rows_no_error(self) -> None:
        from chaoscypher_cli.utils.console import print_table

        buf = StringIO()
        test_console = Console(file=buf, highlight=False, markup=True)

        with patch("chaoscypher_cli.utils.console.get_console", return_value=test_console):
            print_table(["Name"], [])  # no rows — must not raise

        assert "Name" in buf.getvalue()


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
