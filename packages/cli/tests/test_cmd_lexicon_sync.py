# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for lexicon hub commands: search, list, pull, push, remove.

Covers all five lexicon CLI commands through Click's CliRunner with all
network/filesystem I/O mocked.  The test matrix is:
  - search: results found / empty results / client error
  - list:   no packages dir / empty dir / table/json/simple formats / --all flag
  - pull:   happy path / file-exists-no-force / not-found error / with extract /
            not-authed warning / hub-unreachable hint
  - push:   ccx file happy path / not-logged-in / non-ccx file / directory rejected /
            invalid-ccx validation failure / cancelled confirmation / client error /
            hub-unreachable hint / private flag / message
  - remove: ccx file / directory package / version-not-found / not-found /
            with --force / confirm-cancelled / user-scoped path / remove --all /
            path-traversal rejection
  - paths:  list and remove resolve the SAME canonical packages dir
            (CHAOSCYPHER_DATA_DIR honored)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.lexicon.list import list_packages
from chaoscypher_cli.commands.lexicon.pull import pull
from chaoscypher_cli.commands.lexicon.push import push
from chaoscypher_cli.commands.lexicon.remove import remove
from chaoscypher_cli.commands.lexicon.search import format_downloads, search


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_pkg(
    name: str = "test-pkg",
    owner: str = "testuser",
    version: str = "1.0.0",
    description: str = "A test package",
    download_count: int = 42,
) -> MagicMock:
    """Return a mock PackageInfo object."""
    pkg = MagicMock()
    pkg.name = name
    pkg.owner_username = owner
    pkg.version = version
    pkg.description = description
    pkg.download_count = download_count
    pkg.full_name = f"{owner}/{name}"
    return pkg


def _make_upload_result(name: str = "testuser/test-pkg", version: str = "1.0.0") -> MagicMock:
    """Return a mock upload result."""
    result = MagicMock()
    result.name = name
    result.version = version
    return result


def _make_lexicon_client_ctx(search_result=None, download_bytes=b"PKG_DATA", upload_result=None):
    """Return a context manager mock for LexiconClient that can be used in `async with`."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # search returns (packages, total)
    pkgs = search_result if search_result is not None else []
    client.search = AsyncMock(return_value=(pkgs, len(pkgs)))

    # pull / download helpers
    pkg_info = MagicMock()
    pkg_info.version = "1.0.0"
    client.get_package_info = AsyncMock(return_value=pkg_info)
    client.download = AsyncMock(return_value=download_bytes)

    # push helper
    if upload_result is None:
        upload_result = _make_upload_result()
    client.upload = AsyncMock(return_value=upload_result)

    return client


# ---------------------------------------------------------------------------
# format_downloads unit tests (pure function — no Click)
# ---------------------------------------------------------------------------


def test_format_downloads_below_thousand() -> None:
    assert format_downloads(0) == "0"
    assert format_downloads(999) == "999"


def test_format_downloads_thousands() -> None:
    assert format_downloads(1000) == "1.0k"
    assert format_downloads(15300) == "15.3k"


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


class TestSearchCommand:
    """Tests for `chaoscypher lexicon search`."""

    def _invoke(
        self,
        args: list[str],
        auth_config: Any = None,
        search_result: list[Any] | None = None,
        lexicon_url: str = "https://lexicon.test",
    ) -> Any:
        runner = CliRunner()
        client_mock = _make_lexicon_client_ctx(search_result=search_result)

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.search.get_auth_config",
                return_value=auth_config,
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.search.get_lexicon_url",
                return_value=lexicon_url,
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.search.get_settings",
            ) as mock_settings,
            patch(
                "chaoscypher_cli.commands.lexicon.search.LexiconClient",
                return_value=client_mock,
            ),
        ):
            mock_settings.return_value.cli.search_default_limit = 20
            return runner.invoke(search, args)

    def test_search_with_results_prints_table(self) -> None:
        pkg = _make_pkg(name="medical-ontology", owner="john", download_count=2500)
        result = self._invoke(["medical ontology"], search_result=[pkg])
        assert result.exit_code == 0, result.output
        assert "medical-ontology" in result.output
        assert "john" in result.output

    def test_search_empty_results_prints_no_packages_message(self) -> None:
        result = self._invoke(["nonexistent"], search_result=[])
        assert result.exit_code == 0, result.output
        assert "No packages found" in result.output

    def test_search_shows_install_hint(self) -> None:
        pkg = _make_pkg(name="my-pkg", owner="alice")
        result = self._invoke(["my-pkg"], search_result=[pkg])
        assert result.exit_code == 0, result.output
        assert "chaoscypher pull" in result.output
        assert "alice/my-pkg" in result.output

    def test_search_with_tag_filter(self) -> None:
        pkg = _make_pkg(name="bio-pkg", owner="bob")
        result = self._invoke(
            ["research", "--tag", "biomedical", "--limit", "5"],
            search_result=[pkg],
        )
        assert result.exit_code == 0, result.output
        assert "bio-pkg" in result.output

    def test_search_with_author_filter_matches(self) -> None:
        pkg = _make_pkg(name="pkg1", owner="alice")
        result = self._invoke(["query", "--author", "alice"], search_result=[pkg])
        assert result.exit_code == 0, result.output
        assert "pkg1" in result.output

    def test_search_with_author_filter_no_match(self) -> None:
        pkg = _make_pkg(name="pkg1", owner="alice")
        result = self._invoke(["query", "--author", "bob"], search_result=[pkg])
        assert result.exit_code == 0, result.output
        assert "No packages found" in result.output

    def test_search_sort_option(self) -> None:
        pkg = _make_pkg(download_count=5000)
        result = self._invoke(["nlp", "--sort", "downloads"], search_result=[pkg])
        assert result.exit_code == 0, result.output

    def test_search_long_description_truncated(self) -> None:
        long_desc = "A" * 100
        pkg = _make_pkg(description=long_desc)
        result = self._invoke(["test"], search_result=[pkg])
        assert result.exit_code == 0, result.output

    def test_search_client_error_exits_1(self) -> None:
        runner = CliRunner()
        from chaoscypher_core.services.lexicon import LexiconClientError

        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        client_mock.search = AsyncMock(side_effect=LexiconClientError(500, "Server error"))

        with (
            patch("chaoscypher_cli.commands.lexicon.search.get_auth_config", return_value=None),
            patch(
                "chaoscypher_cli.commands.lexicon.search.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.search.get_settings",
            ) as mock_settings,
            patch(
                "chaoscypher_cli.commands.lexicon.search.LexiconClient",
                return_value=client_mock,
            ),
        ):
            mock_settings.return_value.cli.search_default_limit = 20
            result = runner.invoke(search, ["query"])

        assert result.exit_code == 1
        assert "Search failed" in result.output


# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------


class TestListCommand:
    """Tests for `chaoscypher lexicon list`."""

    def test_list_no_packages_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        # Do NOT create packages_dir so it doesn't exist
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, [])
        assert result.exit_code == 0, result.output
        assert "No packages installed" in result.output
        assert "chaoscypher pull" in result.output

    def test_list_empty_packages_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, [])
        assert result.exit_code == 0, result.output
        assert "No packages found" in result.output

    def test_list_table_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        ccx = packages_dir / "my-pkg.ccx"
        ccx.write_bytes(b"FAKE_PKG_DATA_12345")
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, [])
        assert result.exit_code == 0, result.output
        assert "my-pkg" in result.output
        assert "Total:" in result.output

    def test_list_simple_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        ccx = packages_dir / "alpha.ccx"
        ccx.write_bytes(b"DATA")
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, ["--format", "simple"])
        assert result.exit_code == 0, result.output
        assert "alpha" in result.output

    def test_list_json_format(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        ccx = packages_dir / "beta.ccx"
        ccx.write_bytes(b"JSON_DATA")
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, ["--format", "json"])
        assert result.exit_code == 0, result.output
        # Rich may wrap long path lines; verify the key fields appear in output
        output = result.output
        assert '"name": "beta"' in output
        assert '"size": 9' in output

    def test_list_show_all_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        sub = packages_dir / "sub"
        sub.mkdir()
        ccx = sub / "nested.ccx"
        ccx.write_bytes(b"NESTED")
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, ["--all"])
        assert result.exit_code == 0, result.output
        assert "nested" in result.output

    def test_list_large_file_size_kb(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        ccx = packages_dir / "large.ccx"
        ccx.write_bytes(b"X" * 2048)
        with patch(
            "chaoscypher_cli.commands.lexicon.list.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(list_packages, [])
        assert result.exit_code == 0, result.output
        assert "KB" in result.output


# ---------------------------------------------------------------------------
# pull command
# ---------------------------------------------------------------------------


class TestPullCommand:
    """Tests for `chaoscypher lexicon pull`.

    pull.py uses deferred (local) imports inside the function body, so all
    patches must target the SOURCE module, not the pull module namespace.
      - get_auth_config / get_lexicon_url  → chaoscypher_cli.commands.lexicon.login
      - LexiconClient / LexiconClientError → chaoscypher_core.services.lexicon.client
      - extract_archive / format_size      → chaoscypher_core.services.package.archive.*
    """

    def _mock_lexicon_client(self, download_bytes=b"ARCHIVE_BYTES", version="1.0.0"):
        """Build a LexiconClient context-manager mock."""
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        pkg_info = MagicMock()
        pkg_info.version = version
        client.get_package_info = AsyncMock(return_value=pkg_info)
        client.download = AsyncMock(return_value=download_bytes)
        return client

    def test_pull_happy_path_writes_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        client_mock = self._mock_lexicon_client(download_bytes=b"REAL_ARCHIVE")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
            patch(
                "chaoscypher_core.services.package.format_size",
                return_value="12 B",
            ),
        ):
            result = runner.invoke(pull, ["testuser/test-pkg", "--output", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "test-pkg" in result.output
        ccx_files = list(tmp_path.glob("*.ccx"))
        assert len(ccx_files) == 1
        assert ccx_files[0].read_bytes() == b"REAL_ARCHIVE"

    def test_pull_no_auth_shows_warning(self, tmp_path: Path) -> None:
        runner = CliRunner()
        client_mock = self._mock_lexicon_client()

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=None,
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
            patch(
                "chaoscypher_core.services.package.format_size",
                return_value="8 B",
            ),
        ):
            result = runner.invoke(pull, ["public-pkg", "--output", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert "Not logged in" in result.output or "Warning" in result.output

    def test_pull_file_exists_exits_1_without_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        existing = tmp_path / "testuser-test-pkg.ccx"
        existing.write_bytes(b"OLD")

        client_mock = self._mock_lexicon_client()

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(pull, ["testuser/test-pkg", "--output", str(tmp_path)])

        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_pull_force_overwrites_existing_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        existing = tmp_path / "testuser-test-pkg.ccx"
        existing.write_bytes(b"OLD_DATA")

        client_mock = self._mock_lexicon_client(download_bytes=b"NEW_DATA")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
            patch(
                "chaoscypher_core.services.package.format_size",
                return_value="8 B",
            ),
        ):
            result = runner.invoke(
                pull, ["testuser/test-pkg", "--output", str(tmp_path), "--force"]
            )

        assert result.exit_code == 0, result.output
        assert existing.read_bytes() == b"NEW_DATA"

    def test_pull_with_version_uses_versioned_filename(self, tmp_path: Path) -> None:
        runner = CliRunner()
        client_mock = self._mock_lexicon_client(download_bytes=b"V2DATA", version="2.0.0")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
            patch(
                "chaoscypher_core.services.package.format_size",
                return_value="6 B",
            ),
        ):
            result = runner.invoke(
                pull,
                ["testuser/test-pkg", "--version", "2.0.0", "--output", str(tmp_path)],
            )

        assert result.exit_code == 0, result.output
        versioned = tmp_path / "testuser-test-pkg-2.0.0.ccx"
        assert versioned.exists()

    def test_pull_extract_flag_calls_extract_archive(self, tmp_path: Path) -> None:
        runner = CliRunner()
        client_mock = self._mock_lexicon_client(download_bytes=b"ARCHIVE")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
            patch(
                "chaoscypher_core.services.package.format_size",
                return_value="7 B",
            ),
            patch("chaoscypher_core.services.package.extract_archive") as mock_extract,
        ):
            result = runner.invoke(
                pull,
                ["testuser/test-pkg", "--output", str(tmp_path), "--extract"],
            )

        assert result.exit_code == 0, result.output
        mock_extract.assert_called_once()
        assert "Extracting" in result.output

    def test_pull_client_error_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        from chaoscypher_core.services.lexicon import LexiconClientError

        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        client_mock.get_package_info = AsyncMock(
            side_effect=LexiconClientError(404, "Package not found")
        )

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(pull, ["missing/pkg", "--output", str(tmp_path)])

        assert result.exit_code == 1
        assert "Download failed" in result.output

    def test_pull_hub_unreachable_prints_hint_not_traceback(self, tmp_path: Path) -> None:
        """A plain ExternalServiceError (hub down) yields a friendly one-line hint."""
        runner = CliRunner()
        from chaoscypher_core.exceptions import ExternalServiceError

        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        client_mock.get_package_info = AsyncMock(
            side_effect=ExternalServiceError("Lexicon", "Connection refused")
        )

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(pull, ["testuser/test-pkg", "--output", str(tmp_path)])

        assert result.exit_code == 1
        assert "Cannot reach Lexicon Hub" in result.output
        assert "lexicon.test" in result.output
        assert "Traceback" not in result.output
        # The command exited via sys.exit, not an unhandled exception
        assert isinstance(result.exception, SystemExit)

    def test_pull_simple_package_name_no_slash(self, tmp_path: Path) -> None:
        """Package name without '/' sets owner_username to empty string."""
        runner = CliRunner()
        client_mock = self._mock_lexicon_client(download_bytes=b"SIMPLE")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
            patch(
                "chaoscypher_core.services.package.format_size",
                return_value="6 B",
            ),
        ):
            result = runner.invoke(pull, ["simple-pkg", "--output", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "simple-pkg.ccx").exists()


# ---------------------------------------------------------------------------
# push command
# ---------------------------------------------------------------------------


class TestPushCommand:
    """Tests for `chaoscypher lexicon push`.

    The push contract is now: accept a PRE-BUILT CCX 3.0 ``.ccx`` file,
    validate it via ``ccx.open_package(path).validate()``, read display
    metadata from ``pkg.manifest`` and upload the file bytes. A directory is
    no longer a supported input (build the .ccx with
    ``chaoscypher graph package export`` first).

    push.py uses deferred (local) imports inside the function body, so all
    patches must target the SOURCE module, not the push module namespace.
      - get_auth_config / get_lexicon_url  → chaoscypher_cli.commands.lexicon.login
      - LexiconClient / LexiconClientError → chaoscypher_core.services.lexicon.client
      - format_size                        → chaoscypher_core.services.package (re-export)
    Validation uses real CCX 3.0 packages built with ``ccx.PackageBuilder``.
    """

    def _make_ccx(
        self,
        tmp_path: Path,
        filename: str = "my-pkg.ccx",
        name: str = "my-pkg",
        version: str = "1.0.0",
    ) -> Path:
        """Write a real, valid CCX 3.0 package to ``tmp_path/filename``."""
        import ccx

        builder = ccx.PackageBuilder(
            name=name,
            package_version=version,
            license="CC-BY-4.0",
        )
        builder.add_graph("ccx", "knowledge", {"@graph": []}, role="default")
        path = tmp_path / filename
        path.write_bytes(builder.build())
        return path

    def _make_client_mock(self, upload_result=None):
        upload_result = upload_result or _make_upload_result()
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        client_mock.upload = AsyncMock(return_value=upload_result)
        return client_mock

    def test_push_not_logged_in_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path)

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=None,
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
        ):
            result = runner.invoke(push, [str(ccx)])

        assert result.exit_code == 1
        assert "Not logged in" in result.output

    def test_push_ccx_file_happy_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path, name="my-pkg", version="1.0.0")
        upload_result = _make_upload_result(name="testuser/my-pkg", version="1.0.0")
        client_mock = self._make_client_mock(upload_result)

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(push, [str(ccx), "--force"])

        assert result.exit_code == 0, result.output
        client_mock.upload.assert_called_once()
        # Metadata comes from the package manifest, not the filename.
        assert "my-pkg" in result.output
        assert "1.0.0" in result.output

    def test_push_non_ccx_file_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        txt = tmp_path / "file.txt"
        txt.write_bytes(b"text")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
        ):
            result = runner.invoke(push, [str(txt)])

        assert result.exit_code == 1
        assert ".ccx" in result.output

    def test_push_directory_rejected_with_export_hint(self, tmp_path: Path) -> None:
        """A directory is no longer pushable: error and point to export."""
        runner = CliRunner()
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
        ):
            result = runner.invoke(push, [str(pkg_dir)])

        assert result.exit_code == 1
        assert "directory" in result.output.lower()
        assert "graph package export" in result.output

    def test_push_invalid_ccx_validation_failure_exits_1(self, tmp_path: Path) -> None:
        """A file that is not a valid CCX package fails validation."""
        runner = CliRunner()
        bad = tmp_path / "broken.ccx"
        bad.write_bytes(b"NOT_A_REAL_CCX_ZIP")

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
        ):
            result = runner.invoke(push, [str(bad)])

        assert result.exit_code == 1
        assert (
            "Invalid .ccx package" in result.output or "validation failed" in result.output.lower()
        )

    def test_push_confirmation_cancelled(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path)
        client_mock = self._make_client_mock()

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(push, [str(ccx)], input="n\n")

        assert result.exit_code == 0, result.output
        assert "cancelled" in result.output.lower()
        client_mock.upload.assert_not_called()

    def test_push_client_error_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path)
        from chaoscypher_core.services.lexicon import LexiconClientError

        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        client_mock.upload = AsyncMock(
            side_effect=LexiconClientError(401, "Authentication required")
        )

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(push, [str(ccx), "--force"])

        assert result.exit_code == 1
        assert "Upload failed" in result.output

    def test_push_hub_unreachable_prints_hint_not_traceback(self, tmp_path: Path) -> None:
        """A plain ExternalServiceError (hub down) yields a friendly one-line hint."""
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path)
        from chaoscypher_core.exceptions import ExternalServiceError

        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=False)
        client_mock.upload = AsyncMock(
            side_effect=ExternalServiceError("Lexicon", "Connection refused")
        )

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(push, [str(ccx), "--force"])

        assert result.exit_code == 1
        assert "Cannot reach Lexicon Hub" in result.output
        assert "lexicon.test" in result.output
        assert "Traceback" not in result.output
        # The command exited via sys.exit, not an unhandled exception
        assert isinstance(result.exception, SystemExit)

    def test_push_private_flag(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path)
        client_mock = self._make_client_mock()

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(push, [str(ccx), "--private", "--force"])

        assert result.exit_code == 0, result.output
        assert "Private" in result.output

    def test_push_with_message(self, tmp_path: Path) -> None:
        runner = CliRunner()
        ccx = self._make_ccx(tmp_path)
        client_mock = self._make_client_mock()

        with (
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_auth_config",
                return_value=MagicMock(token="tok"),
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.login.get_lexicon_url",
                return_value="https://lexicon.test",
            ),
            patch(
                "chaoscypher_core.services.lexicon.LexiconClient",
                return_value=client_mock,
            ),
        ):
            result = runner.invoke(push, [str(ccx), "--message", "Initial release", "--force"])

        assert result.exit_code == 0, result.output
        assert "Initial release" in result.output


# ---------------------------------------------------------------------------
# remove command
# ---------------------------------------------------------------------------


class TestRemoveCommand:
    """Tests for `chaoscypher lexicon remove`."""

    def test_remove_package_not_found_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["nonexistent-pkg"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_remove_ccx_file_with_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        ccx = packages_dir / "mypkg.ccx"
        ccx.write_bytes(b"DATA")

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["mypkg.ccx", "--force"])

        assert result.exit_code == 0, result.output
        assert not ccx.exists()
        assert "removed" in result.output.lower()

    def test_remove_directory_package_with_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        pkg_dir = packages_dir / "my-package"
        pkg_dir.mkdir()
        (pkg_dir / "data.txt").write_text("content")

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["my-package", "--force"])

        assert result.exit_code == 0, result.output
        assert not pkg_dir.exists()
        assert "removed" in result.output.lower()

    def test_remove_user_scoped_package(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        user_dir = packages_dir / "alice"
        user_dir.mkdir(parents=True)
        pkg_dir = user_dir / "medical-ontology"
        pkg_dir.mkdir()
        (pkg_dir / "file.txt").write_text("content")

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["alice/medical-ontology", "--force"])

        assert result.exit_code == 0, result.output
        assert not pkg_dir.exists()
        assert "removed" in result.output.lower()

    def test_remove_specific_version(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        pkg_dir = packages_dir / "my-package"
        version_dir = pkg_dir / "1.0.0"
        version_dir.mkdir(parents=True)
        (version_dir / "manifest.json").write_text("{}")

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["my-package", "--version", "1.0.0", "--force"])

        assert result.exit_code == 0, result.output
        assert not version_dir.exists()

    def test_remove_version_not_found_exits_1(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        pkg_dir = packages_dir / "my-package"
        other_version = pkg_dir / "2.0.0"
        other_version.mkdir(parents=True)

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["my-package", "--version", "1.0.0", "--force"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_remove_all_versions(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        pkg_dir = packages_dir / "my-package"
        v1 = pkg_dir / "1.0.0"
        v2 = pkg_dir / "2.0.0"
        v1.mkdir(parents=True)
        v2.mkdir(parents=True)

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["my-package", "--all", "--force"])

        assert result.exit_code == 0, result.output
        assert not v1.exists()
        assert not v2.exists()

    def test_remove_confirmation_cancelled(self, tmp_path: Path) -> None:
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        ccx = packages_dir / "mypkg.ccx"
        ccx.write_bytes(b"DATA")

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            # Input "n" to cancel the Confirm prompt
            result = runner.invoke(remove, ["mypkg.ccx"], input="n\n")

        assert result.exit_code == 0, result.output
        assert ccx.exists()  # file was NOT removed
        assert "Cancelled" in result.output

    def test_remove_unscoped_package_found_in_user_dir(self, tmp_path: Path) -> None:
        """Without slash, remove searches user dirs for the package."""
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        user_dir = packages_dir / "bob"
        user_dir.mkdir(parents=True)
        pkg_dir = user_dir / "research-corpus"
        pkg_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["research-corpus", "--force"])

        assert result.exit_code == 0, result.output
        assert not pkg_dir.exists()

    def test_remove_dir_no_versions_uses_remove_all_path(self, tmp_path: Path) -> None:
        """--all on a dir with no sub-dirs should fall back to removing the dir itself."""
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        pkg_dir = packages_dir / "empty-pkg"
        pkg_dir.mkdir(parents=True)
        # No version sub-dirs — iterdir() returns empty

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["empty-pkg", "--all", "--force"])

        assert result.exit_code == 0, result.output
        assert not pkg_dir.exists()

    @pytest.mark.parametrize(
        "bad_name",
        [
            "..",
            "../x",
            "..\\..\\x",
            "/abs/path",
            "C:/evil",
            "C:\\evil",
            "alice/../escape",
            "alice/./pkg",
            "pkg/",
            "sub\\pkg",
        ],
    )
    def test_remove_rejects_path_traversal_names(self, bad_name: str, tmp_path: Path) -> None:
        """Names that could resolve outside the packages dir are rejected up front."""
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ) as mock_dir:
            result = runner.invoke(remove, [bad_name, "--force"])

        assert result.exit_code == 1
        assert "Invalid package name" in result.output
        # Validation happens before ANY filesystem operation
        mock_dir.assert_not_called()

    def test_remove_traversal_cannot_delete_outside_packages_dir(self, tmp_path: Path) -> None:
        """A relative-traversal name must never reach (or delete) a sibling directory."""
        runner = CliRunner()
        packages_dir = tmp_path / "data" / "packages"
        packages_dir.mkdir(parents=True)
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / "file.txt").write_text("precious")

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["../../victim", "--force"])

        assert result.exit_code == 1
        assert "Invalid package name" in result.output
        assert victim.exists()
        assert (victim / "file.txt").read_text() == "precious"

    def test_remove_rejects_traversal_version(self, tmp_path: Path) -> None:
        """--version '..' would resolve to the packages dir itself — rejected."""
        runner = CliRunner()
        packages_dir = tmp_path / "packages"
        pkg_dir = packages_dir / "my-package"
        pkg_dir.mkdir(parents=True)

        with patch(
            "chaoscypher_cli.commands.lexicon.remove.get_packages_dir",
            return_value=packages_dir,
        ):
            result = runner.invoke(remove, ["my-package", "--version", "..", "--force"])

        assert result.exit_code == 1
        assert "Invalid version" in result.output
        assert packages_dir.exists()
        assert pkg_dir.exists()


# ---------------------------------------------------------------------------
# packages-dir resolution (list and remove must agree)
# ---------------------------------------------------------------------------


class TestPackagesDirResolution:
    """`lexicon list` and `lexicon remove` must scan the SAME packages dir."""

    def test_list_and_remove_share_canonical_packages_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import chaoscypher_cli.commands.lexicon.list as list_module
        import chaoscypher_cli.commands.lexicon.remove as remove_module

        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path / "data"))

        # Both commands use the one canonical resolver from utils.paths ...
        assert list_module.get_packages_dir is remove_module.get_packages_dir
        # ... and that resolver honors CHAOSCYPHER_DATA_DIR identically for both.
        resolved = list_module.get_packages_dir()
        assert resolved == remove_module.get_packages_dir()
        assert resolved == tmp_path / "data" / "packages"
