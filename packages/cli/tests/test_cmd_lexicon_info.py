# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for chaoscypher_cli.commands.lexicon.info (info command)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.lexicon.info import info
from chaoscypher_core.services.lexicon import AuthConfig, LexiconClientError
from chaoscypher_core.services.lexicon.client import PackageInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_creds_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    d.mkdir()
    return d


def _write_creds(config_dir: Path, username: str = "alice", token: str = "tok123") -> None:  # noqa: S107
    creds: dict[str, Any] = {
        "lexicon_url": "https://lexicon.example.com",
        "token": token,
        "username": username,
    }
    (config_dir / "auth.json").write_text(json.dumps(creds))


def _make_package_info(
    name: str = "medical-ontology",
    owner: str = "john",
    description: str = "Test package",
    version: str = "1.0.0",
) -> PackageInfo:
    return PackageInfo(
        id="pkg-001",
        name=name,
        owner_username=owner,
        description=description,
        star_count=5,
        version_count=3,
        download_count=100,
        created_at=1700000000000,
        updated_at=1700100000000,
        version=version,
        conformance_classes=["ccx-core"],
        is_signed=True,
    )


def _make_archive_info() -> MagicMock:
    ai = MagicMock()
    ai.compressed_size_formatted = "1.2 MB"
    ai.uncompressed_size_formatted = "4.8 MB"
    ai.file_count = 5
    ai.contents = ["manifest.json", "nodes.json", "edges.json", "lenses.json", "templates.json"]
    return ai


# ---------------------------------------------------------------------------
# Hub info — happy path (authenticated)
# ---------------------------------------------------------------------------


def test_info_hub_authenticated(tmp_path: Path) -> None:
    """Info: john/medical-ontology fetches metadata and prints it."""
    config_dir = _make_creds_dir(tmp_path)
    _write_creds(config_dir)
    pkg = _make_package_info()

    async def fake_get_package_info(owner: str, name: str, version: Any) -> PackageInfo:
        return pkg

    mock_client = AsyncMock()
    mock_client.get_package_info = fake_get_package_info
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_auth_config",
            return_value=AuthConfig(token="tok", username="alice"),
        ),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["john/medical-ontology"])

    assert result.exit_code == 0, result.output
    assert "medical-ontology" in result.output
    assert "john" in result.output


def test_info_hub_with_version(tmp_path: Path) -> None:
    """--version flag is passed to LexiconClient."""
    config_dir = _make_creds_dir(tmp_path)
    _write_creds(config_dir)
    pkg = _make_package_info(version="2.0.0")

    async def fake_get_package_info(owner: str, name: str, version: Any) -> PackageInfo:
        return pkg

    mock_client = AsyncMock()
    mock_client.get_package_info = fake_get_package_info
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.login.get_config_dir", return_value=config_dir),
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_auth_config",
            return_value=AuthConfig(token="tok", username="alice"),
        ),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["john/medical-ontology", "--version", "2.0.0"])

    assert result.exit_code == 0, result.output
    assert "2.0.0" in result.output


# ---------------------------------------------------------------------------
# Hub info — unauthenticated (no auth, still works for public packages)
# ---------------------------------------------------------------------------


def test_info_hub_unauthenticated(tmp_path: Path) -> None:
    """Info works for public packages even when not logged in."""
    pkg = _make_package_info()

    async def fake_get_package_info(owner: str, name: str, version: Any) -> PackageInfo:
        return pkg

    mock_client = AsyncMock()
    mock_client.get_package_info = fake_get_package_info
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_auth_config",
            return_value=None,
        ),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["john/medical-ontology"])

    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Hub info — error branches
# ---------------------------------------------------------------------------


def test_info_hub_bad_package_format_exits_nonzero() -> None:
    """Package name without '/' prints error and exits 1."""
    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.info.get_auth_config", return_value=None),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["badpackagename"])

    assert result.exit_code == 1
    assert "owner/name" in result.output


def test_info_hub_404_exits_nonzero() -> None:
    """LexiconClientError 404 prints 'Package not found' and exits 1."""
    mock_client = AsyncMock()
    mock_client.get_package_info = AsyncMock(
        side_effect=LexiconClientError(status_code=404, message="Package not found")
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.info.get_auth_config", return_value=None),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["john/missing-package"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_info_hub_other_error_exits_nonzero() -> None:
    """Non-404 LexiconClientError prints hub error and exits 1."""
    mock_client = AsyncMock()
    mock_client.get_package_info = AsyncMock(
        side_effect=LexiconClientError(status_code=500, message="Internal server error")
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.info.get_auth_config", return_value=None),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["john/broken-package"])

    assert result.exit_code == 1
    assert "Hub error" in result.output or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# Hub info — package with created_at and updated_at (branches)
# ---------------------------------------------------------------------------


def test_info_hub_shows_timestamps(tmp_path: Path) -> None:
    """Non-zero created_at / updated_at are printed."""
    pkg = _make_package_info()
    # Already has non-zero timestamps from helper

    async def fake_get_package_info(owner: str, name: str, version: Any) -> PackageInfo:
        return pkg

    mock_client = AsyncMock()
    mock_client.get_package_info = fake_get_package_info
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.info.get_auth_config", return_value=None),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["john/medical-ontology"])

    assert result.exit_code == 0, result.output
    # Timestamps shown as-is (integer)
    assert "Created" in result.output or "Updated" in result.output


def test_info_hub_no_timestamps_skipped() -> None:
    """Zero/falsy created_at / updated_at are not printed."""
    pkg = PackageInfo(
        id="pkg-002",
        name="no-ts-package",
        owner_username="acme",
        description="No timestamps",
        created_at=0,
        updated_at=0,
    )

    async def fake_get_package_info(owner: str, name: str, version: Any) -> PackageInfo:
        return pkg

    mock_client = AsyncMock()
    mock_client.get_package_info = fake_get_package_info
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    runner = CliRunner()
    with (
        patch("chaoscypher_cli.commands.lexicon.info.LexiconClient", return_value=mock_client),
        patch("chaoscypher_cli.commands.lexicon.info.get_auth_config", return_value=None),
        patch(
            "chaoscypher_cli.commands.lexicon.info.get_lexicon_url",
            return_value="https://lexicon.example.com",
        ),
    ):
        result = runner.invoke(info, ["acme/no-ts-package"])

    assert result.exit_code == 0, result.output
    assert "Created" not in result.output
    assert "Updated" not in result.output


# ---------------------------------------------------------------------------
# Local info — happy path
# ---------------------------------------------------------------------------


def test_info_local_file_happy_path(tmp_path: Path) -> None:
    """--local reads archive info from a .ccx file."""
    fake_ccx = tmp_path / "my-package.ccx"
    fake_ccx.write_bytes(b"fake ccx content")
    archive_info = _make_archive_info()

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.info.get_archive_info", return_value=archive_info):
        result = runner.invoke(info, [str(fake_ccx), "--local"])

    assert result.exit_code == 0, result.output
    assert "my-package.ccx" in result.output
    assert "1.2 MB" in result.output
    assert "5" in result.output  # file_count


def test_info_local_file_more_than_10_files(tmp_path: Path) -> None:
    """When file_count > 10, '... and N more' line is printed."""
    fake_ccx = tmp_path / "big-package.ccx"
    fake_ccx.write_bytes(b"fake")
    archive_info = MagicMock()
    archive_info.compressed_size_formatted = "5 MB"
    archive_info.uncompressed_size_formatted = "20 MB"
    archive_info.file_count = 15
    archive_info.contents = [f"file{i}.json" for i in range(15)]

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.info.get_archive_info", return_value=archive_info):
        result = runner.invoke(info, [str(fake_ccx), "--local"])

    assert result.exit_code == 0, result.output
    assert "5 more" in result.output


def test_info_local_file_non_ccx_extension(tmp_path: Path) -> None:
    """Non-.ccx file prints a warning but still proceeds."""
    fake_file = tmp_path / "my-package.zip"
    fake_file.write_bytes(b"fake content")
    archive_info = _make_archive_info()

    runner = CliRunner()
    with patch("chaoscypher_cli.commands.lexicon.info.get_archive_info", return_value=archive_info):
        result = runner.invoke(info, [str(fake_file), "--local"])

    assert result.exit_code == 0, result.output
    assert "Warning" in result.output or "ccx" in result.output.lower()


# ---------------------------------------------------------------------------
# Local info — error branches
# ---------------------------------------------------------------------------


def test_info_local_file_not_found_exits_nonzero(tmp_path: Path) -> None:
    """Missing local file prints error and exits 1."""
    missing = str(tmp_path / "nonexistent.ccx")

    runner = CliRunner()
    result = runner.invoke(info, [missing, "--local"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "File not found" in result.output


def test_info_local_file_read_error_exits_nonzero(tmp_path: Path) -> None:
    """get_archive_info raising an exception prints error and exits 1."""
    fake_ccx = tmp_path / "corrupt.ccx"
    fake_ccx.write_bytes(b"junk")

    runner = CliRunner()
    with patch(
        "chaoscypher_cli.commands.lexicon.info.get_archive_info",
        side_effect=Exception("Bad zip"),
    ):
        result = runner.invoke(info, [str(fake_ccx), "--local"])

    assert result.exit_code == 1
    assert "Failed to read" in result.output or "Bad zip" in result.output
