# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for ``chaoscypher diagnostics`` command.

Covers:
- Default output path (no --output flag)
- Explicit --output path
- DB file present / absent
- Active-database resolution (env-driven db switch) and --database override
- Log directory present (with log files) / absent
- Path resolution failure (exception branch)
- Bundle saved message in output

``DiagnosticCollector`` is imported lazily inside the command function
body, so it is patched at its *source* module. The database directory is
resolved via ``chaoscypher_cli.engine_config.data_dir`` (the CLI's single
path-resolution authority — same as every db/* command) combined with
``get_database_name()``, so tests patch ``engine_config.data_dir`` and
drive the database via the ``CHAOSCYPHER_DATABASE`` env var / --database.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.diagnostics import diagnostics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _patch_diag(
    data_dir: Path | None,
    mock_collector: MagicMock,
    *,
    data_dir_raises: bool = False,
) -> Any:
    """Patch the lazy imports used by the diagnostics command.

    Yields the DiagnosticCollector class mock so tests can assert on the
    ``db_path`` it was constructed with.
    """
    if data_dir_raises:
        dd_patch = patch(
            "chaoscypher_cli.engine_config.data_dir",
            side_effect=RuntimeError("no config"),
        )
    else:
        dd_patch = patch(
            "chaoscypher_cli.engine_config.data_dir",
            return_value=data_dir,
        )
    with dd_patch:
        with patch(
            "chaoscypher_core.services.diagnostics.DiagnosticCollector",
            return_value=mock_collector,
        ) as mock_cls:
            yield mock_cls


def _make_collector(tmp_path: Path) -> MagicMock:
    """Return a MagicMock DiagnosticCollector whose export_bundle writes a tiny zip."""
    mock_collector = MagicMock()

    def _export(path: Path, **_kw: Any) -> Path:
        path.write_bytes(b"PK\x03\x04")
        return path

    mock_collector.export_bundle.side_effect = _export
    return mock_collector


def _make_db(data_dir: Path, name: str) -> Path:
    """Create databases/<name>/app.db under *data_dir* and return its path."""
    db_dir = data_dir / "databases" / name
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "app.db"
    db_file.write_bytes(b"SQLite format 3\x00")
    return db_file


_NO_DB_ENV = {"CHAOSCYPHER_DATABASE": None}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDiagnosticsCommand:
    """End-to-end tests for the diagnostics command through CliRunner."""

    def test_default_output_path_creates_zip(self, tmp_path: Path) -> None:
        """Without --output, writes chaoscypher-diagnostics-<ts>.zip."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mock_collector = _make_collector(tmp_path)

        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            with _patch_diag(data_dir, mock_collector):
                result = runner.invoke(diagnostics, env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "Bundle saved" in result.output
        mock_collector.export_bundle.assert_called_once()

    def test_explicit_output_path(self, tmp_path: Path) -> None:
        """--output writes to the specified path."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        out_file = tmp_path / "my-diag.zip"
        mock_collector = _make_collector(tmp_path)

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["--output", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "Bundle saved" in result.output
        called_path = mock_collector.export_bundle.call_args[0][0]
        assert called_path == out_file

    def test_db_file_present_shows_database_found(self, tmp_path: Path) -> None:
        """When app.db exists, output says 'Database found'."""
        data_dir = tmp_path / "data"
        _make_db(data_dir, "default")
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "Database found" in result.output
        mock_collector.export_bundle.assert_called_once()

    def test_db_file_absent_shows_no_database(self, tmp_path: Path) -> None:
        """When app.db is missing, output says 'No database found'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "No database found" in result.output

    def test_collects_active_database_after_switch(self, tmp_path: Path) -> None:
        """After a db switch (env), the bundle targets the ACTIVE database.

        The command previously hardcoded databases/default and ignored the
        active database entirely.
        """
        data_dir = tmp_path / "data"
        _make_db(data_dir, "default")
        myproj_db = _make_db(data_dir, "myproj")
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector) as mock_cls:
            result = runner.invoke(
                diagnostics,
                ["-o", str(out_file)],
                env={"CHAOSCYPHER_DATABASE": "myproj"},
            )

        assert result.exit_code == 0, result.output
        assert "Database found" in result.output
        assert mock_cls.call_args.kwargs["db_path"] == myproj_db

    def test_database_flag_overrides_active(self, tmp_path: Path) -> None:
        """--database overrides the active-database resolution."""
        data_dir = tmp_path / "data"
        _make_db(data_dir, "myproj")
        other_db = _make_db(data_dir, "other")
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector) as mock_cls:
            result = runner.invoke(
                diagnostics,
                ["-o", str(out_file), "--database", "other"],
                env={"CHAOSCYPHER_DATABASE": "myproj"},
            )

        assert result.exit_code == 0, result.output
        assert mock_cls.call_args.kwargs["db_path"] == other_db

    def test_log_dir_present_shows_log_count(self, tmp_path: Path) -> None:
        """When log dir exists with .log files, output shows log file count."""
        data_dir = tmp_path / "data"
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "app.log").write_text("line1\n")
        (log_dir / "worker.log").write_text("line2\n")
        (log_dir / "other.txt").write_text("x")  # non-.log, not counted

        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "Log directory" in result.output
        assert "2" in result.output  # 2 .log files

    def test_log_dir_absent_shows_no_log_directory(self, tmp_path: Path) -> None:
        """When logs/ is absent, output says 'No log directory found'."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "No log directory found" in result.output

    def test_config_load_failure_continues_with_defaults(self, tmp_path: Path) -> None:
        """When path resolution raises, the command shows 'Could not load CLI
        config' and still runs the collector with None paths.
        """
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(None, mock_collector, data_dir_raises=True):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
        assert "Could not load CLI config" in result.output
        assert "Bundle saved" in result.output
        mock_collector.export_bundle.assert_called_once()

    def test_attach_message_in_output(self, tmp_path: Path) -> None:
        """Instructs user to attach file to bug report."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert "Attach this file" in result.output

    def test_short_output_flag(self, tmp_path: Path) -> None:
        """--output and -o are equivalent."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        mock_collector = _make_collector(tmp_path)
        out_file = tmp_path / "diag2.zip"

        runner = CliRunner()
        with _patch_diag(data_dir, mock_collector):
            result = runner.invoke(diagnostics, ["-o", str(out_file)], env=_NO_DB_ENV)

        assert result.exit_code == 0, result.output
