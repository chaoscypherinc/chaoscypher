# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ``chaoscypher diagnostics`` command.

Covers:
- Default output path (no --output flag)
- Explicit --output path
- DB file present
- DB file absent
- Log directory present (with log files)
- Log directory absent
- Config load failure (exception branch)
- Bundle saved message in output

Both ``get_settings`` and ``DiagnosticCollector`` are imported lazily inside
the command function body, so they are patched at their *source* modules
(``chaoscypher_core.app_config.get_settings`` and
``chaoscypher_core.services.diagnostics.DiagnosticCollector``). Engine config
(including ``paths.data_dir``) reads from settings.yaml via app_config as of
the 2026-06 config unification.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.diagnostics import diagnostics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_config(data_dir: str) -> Any:
    """Build a SimpleNamespace shaped like the app settings diagnostics reads."""
    return SimpleNamespace(
        paths=SimpleNamespace(data_dir=data_dir),
    )


@contextmanager
def _patch_diag(
    fake_cfg: Any | None,
    mock_collector: MagicMock,
    *,
    cfg_raises: bool = False,
) -> Any:
    """Patch both lazy imports used by the diagnostics command."""
    if cfg_raises:
        cfg_patch = patch(
            "chaoscypher_core.app_config.get_settings",
            side_effect=RuntimeError("no settings"),
        )
    else:
        cfg_patch = patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=fake_cfg,
        )
    with cfg_patch:
        with patch(
            "chaoscypher_core.services.diagnostics.DiagnosticCollector",
            return_value=mock_collector,
        ):
            yield


def _make_collector(tmp_path: Path) -> MagicMock:
    """Return a MagicMock DiagnosticCollector whose export_bundle writes a tiny zip."""
    mock_collector = MagicMock()

    def _export(path: Path, **_kw: Any) -> Path:
        path.write_bytes(b"PK\x03\x04")
        return path

    mock_collector.export_bundle.side_effect = _export
    return mock_collector


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDiagnosticsCommand:
    """End-to-end tests for the diagnostics command through CliRunner."""

    def test_default_output_path_creates_zip(self, tmp_path: Path) -> None:
        """Without --output, writes chaoscypher-diagnostics-<ts>.zip."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with _patch_diag(fake_cfg, mock_collector):
                result = runner.invoke(diagnostics)

        assert result.exit_code == 0, result.output
        assert "Bundle saved" in result.output
        mock_collector.export_bundle.assert_called_once()

    def test_explicit_output_path(self, tmp_path: Path) -> None:
        """--output writes to the specified path."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        out_file = tmp_path / "my-diag.zip"
        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["--output", str(out_file)])

        assert result.exit_code == 0, result.output
        assert "Bundle saved" in result.output
        called_path = mock_collector.export_bundle.call_args[0][0]
        assert called_path == out_file

    def test_db_file_present_shows_database_found(self, tmp_path: Path) -> None:
        """When app.db exists, output says 'Database found'."""
        data_dir = tmp_path / "data"
        db_dir = data_dir / "databases" / "default"
        db_dir.mkdir(parents=True)
        db_file = db_dir / "app.db"
        db_file.write_bytes(b"SQLite format 3\x00")

        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert result.exit_code == 0, result.output
        assert "Database found" in result.output
        mock_collector.export_bundle.assert_called_once()

    def test_db_file_absent_shows_no_database(self, tmp_path: Path) -> None:
        """When app.db is missing, output says 'No database found'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert result.exit_code == 0, result.output
        assert "No database found" in result.output

    def test_log_dir_present_shows_log_count(self, tmp_path: Path) -> None:
        """When log dir exists with .log files, output shows log file count."""
        data_dir = tmp_path / "data"
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "app.log").write_text("line1\n")
        (log_dir / "worker.log").write_text("line2\n")
        (log_dir / "other.txt").write_text("x")  # non-.log, not counted

        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert result.exit_code == 0, result.output
        assert "Log directory" in result.output
        assert "2" in result.output  # 2 .log files

    def test_log_dir_absent_shows_no_log_directory(self, tmp_path: Path) -> None:
        """When logs/ is absent, output says 'No log directory found'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert result.exit_code == 0, result.output
        assert "No log directory found" in result.output

    def test_config_load_failure_continues_with_defaults(self, tmp_path: Path) -> None:
        """When get_config raises, the command shows 'Could not load CLI config'
        and still runs the collector with None paths.
        """
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(None, mock_collector, cfg_raises=True):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert result.exit_code == 0, result.output
        assert "Could not load CLI config" in result.output
        assert "Bundle saved" in result.output
        mock_collector.export_bundle.assert_called_once()

    def test_attach_message_in_output(self, tmp_path: Path) -> None:
        """Instructs user to attach file to bug report."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert "Attach this file" in result.output

    def test_short_output_flag(self, tmp_path: Path) -> None:
        """--output and -o are equivalent."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        fake_cfg = _fake_config(str(data_dir))
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag2.zip"

        runner = CliRunner()
        with _patch_diag(fake_cfg, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)])

        assert result.exit_code == 0, result.output
