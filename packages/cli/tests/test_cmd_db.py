# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CliRunner coverage for the ``chaoscypher db`` command group.

Covers the multi-database management subcommands that manage database
directories under a data dir:

- create: new-db dir creation + duplicate-error + invalid-name guards.
- list:   table / --json / --quiet / empty, marking the current db.
- current: prints active db, plain and --verbose.
- switch: persists the active db to settings.yaml + unknown-db error.
- delete: removal + confirm prompt + default/current/not-found guards.
- info:   stats for a db + not-found + --json.

The current-database readers resolve the active db through
``get_database_name`` (settings.yaml ``current_database`` → env → "default")
as of the 2026-06 config unification; tests patch that at the COMMAND module
namespace (``_patch_current_db``) or, for ``switch`` persistence, use the
``isolated_settings`` fixture and assert on the on-disk settings.yaml. The
data-dir resolution (``get_databases_dir``) is still patched directly.
``tmp_path`` holds all on-disk state; real ``app.db`` files are written there
so ``get_database_info`` returns real filesystem metadata. ``get_context`` is
patched where the command looks it up so no real Alembic / Ollama context
loads.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from chaoscypher_cli.commands.db.create import create, validate_database_name
from chaoscypher_cli.commands.db.current import current
from chaoscypher_cli.commands.db.delete import delete
from chaoscypher_cli.commands.db.info import info
from chaoscypher_cli.commands.db.list import (
    get_database_info,
    get_databases_dir,
    list_databases,
)
from chaoscypher_cli.commands.db.switch import switch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(databases_dir: Path, name: str, size: int = 4096) -> Path:
    """Create a fake initialized database dir with a real ``app.db`` file.

    ``get_database_info`` keys off the presence of ``app.db`` and its
    ``stat()``, so writing a real file gives the commands real filesystem
    metadata to render.
    """
    db_path = databases_dir / name
    db_path.mkdir(parents=True, exist_ok=True)
    (db_path / "app.db").write_bytes(b"x" * size)
    return db_path


def _patch_current_db(module: str, name: str) -> Any:
    """Patch ``get_database_name`` in a db command module to return ``name``.

    The current-database readers resolve the active db through
    ``chaoscypher_cli.context.get_database_name`` (settings.yaml current_database
    → env → "default") as of the 2026-06 config unification.
    """
    return patch(f"chaoscypher_cli.commands.db.{module}.get_database_name", return_value=name)


# ===========================================================================
# create
# ===========================================================================


class TestCreate:
    """``db create`` makes a new db dir / registry entry and guards dupes."""

    def test_create_invokes_context_and_reports_location(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        created_ctx = MagicMock()
        created_ctx.database_dir = databases_dir / "my-project"

        with patch(
            "chaoscypher_cli.commands.db.create.get_databases_dir",
            return_value=databases_dir,
        ):
            with patch(
                "chaoscypher_cli.commands.db.create.get_context",
                return_value=created_ctx,
            ) as mock_get_context:
                # Force the narrow, no-TTY width a CI runner uses so the long
                # "Location:" path is not hard-wrapped mid-token (regression).
                result = runner.invoke(create, ["my-project"], env={"COLUMNS": "80"})

        assert result.exit_code == 0, result.output
        assert "Created database 'my-project'" in result.output
        # The directory is materialized by get_context(auto_connect=True).
        mock_get_context.assert_called_once_with(database_name="my-project", auto_connect=True)
        assert str(created_ctx.database_dir) in result.output

    def test_create_rejects_invalid_name(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.db.create.get_databases_dir",
            return_value=databases_dir,
        ):
            with patch("chaoscypher_cli.commands.db.create.get_context") as mock_get_context:
                result = runner.invoke(create, ["bad name!"])

        assert result.exit_code == 1
        assert "Invalid database name" in result.output
        mock_get_context.assert_not_called()

    def test_create_duplicate_errors(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "existing")

        with patch(
            "chaoscypher_cli.commands.db.create.get_databases_dir",
            return_value=databases_dir,
        ):
            with patch("chaoscypher_cli.commands.db.create.get_context") as mock_get_context:
                result = runner.invoke(create, ["existing"])

        assert result.exit_code == 1
        assert "already exists" in result.output
        mock_get_context.assert_not_called()

    def test_create_handles_context_failure(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.db.create.get_databases_dir",
            return_value=databases_dir,
        ):
            with patch(
                "chaoscypher_cli.commands.db.create.get_context",
                side_effect=RuntimeError("disk full"),
            ):
                result = runner.invoke(create, ["my-project"])

        assert result.exit_code == 1
        assert "Failed to create database" in result.output
        assert "disk full" in result.output

    def test_validate_database_name(self) -> None:
        assert validate_database_name("my-project")
        assert validate_database_name("research_2024")
        assert validate_database_name("abc123")
        assert not validate_database_name("bad name")
        assert not validate_database_name("bad!name")
        assert not validate_database_name("")


# ===========================================================================
# list
# ===========================================================================


class TestList:
    """``db list`` renders multiple/just-default dbs and marks current."""

    def test_list_table_marks_current(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "default")
        _make_db(databases_dir, "alpha")

        with _patch_current_db("list", "alpha"):
            with patch(
                "chaoscypher_cli.commands.db.list.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(list_databases, [])

        assert result.exit_code == 0, result.output
        assert "alpha" in result.output
        assert "default" in result.output
        assert "current" in result.output

    def test_list_quiet_prints_only_names(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "default")
        _make_db(databases_dir, "alpha")

        with _patch_current_db("list", "default"):
            with patch(
                "chaoscypher_cli.commands.db.list.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(list_databases, ["--quiet"])

        assert result.exit_code == 0, result.output
        # Quiet output is just names, one per line — no table chrome.
        lines = [line.strip() for line in result.output.splitlines() if line.strip()]
        assert lines == ["alpha", "default"]
        assert "Size" not in result.output

    def test_list_json_emits_records(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "default")

        with _patch_current_db("list", "default"):
            with patch(
                "chaoscypher_cli.commands.db.list.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(list_databases, ["--json"])

        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "default"
        assert data[0]["is_current"] is True

    def test_list_json_not_wrapped_on_narrow_console(self, tmp_path: Path) -> None:
        """JSON output must parse even on a narrow, no-TTY console (CI runner).

        Regression for the bug where Rich hard-wrapped ``--json`` output to the
        80-column default used when stdout is not a terminal, inserting newlines
        mid-token so downstream ``json.loads`` raised ``Invalid control
        character``. The long ``tmp_path`` makes the embedded path exceed 80
        cols, so any width-based wrapping would corrupt the document.
        """
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "default")

        with _patch_current_db("list", "default"):
            with patch(
                "chaoscypher_cli.commands.db.list.get_databases_dir",
                return_value=databases_dir,
            ):
                # Force the narrow width a non-TTY CI runner sees.
                result = runner.invoke(list_databases, ["--json"], env={"COLUMNS": "80"})

        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)  # must not raise
        assert data[0]["path"] == str(databases_dir / "default")

    def test_list_empty_shows_hint(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        with _patch_current_db("list", "default"):
            with patch(
                "chaoscypher_cli.commands.db.list.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(list_databases, [])

        assert result.exit_code == 0, result.output
        assert "No databases found" in result.output

    def test_list_skips_non_initialized_dirs(self, tmp_path: Path) -> None:
        """A dir without app.db is not a valid database and is skipped."""
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "real")
        (databases_dir / "empty").mkdir()  # no app.db

        with _patch_current_db("list", "real"):
            with patch(
                "chaoscypher_cli.commands.db.list.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(list_databases, ["--quiet"])

        lines = [line.strip() for line in result.output.splitlines() if line.strip()]
        assert lines == ["real"]

    def test_get_databases_dir_uses_env_data_dir(self, tmp_path: Path, monkeypatch) -> None:
        # Resolution follows CHAOSCYPHER_DATA_DIR (like CLIContext), not
        # cli.yaml — see test_get_databases_dir_resolves_from_env_not_cli_yaml.
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
        assert get_databases_dir() == tmp_path / "databases"

    def test_get_database_info_none_when_uninitialized(self, tmp_path: Path) -> None:
        assert get_database_info("ghost", tmp_path / "ghost") is None


# ===========================================================================
# current
# ===========================================================================


class TestCurrent:
    """``db current`` prints the active db, plain and verbose."""

    def test_current_plain_prints_name(self, tmp_path: Path) -> None:
        runner = CliRunner()

        with _patch_current_db("current", "alpha"):
            result = runner.invoke(current, [])

        assert result.exit_code == 0, result.output
        assert "alpha" in result.output

    def test_current_verbose_with_info(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "alpha", size=2048)

        with _patch_current_db("current", "alpha"):
            with patch(
                "chaoscypher_cli.commands.db.current.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(current, ["--verbose"])

        assert result.exit_code == 0, result.output
        assert "Current database:" in result.output
        assert "alpha" in result.output
        assert "Location:" in result.output
        assert "Size:" in result.output

    def test_current_verbose_uninitialized(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        with _patch_current_db("current", "ghost"):
            with patch(
                "chaoscypher_cli.commands.db.current.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(current, ["-v"])

        assert result.exit_code == 0, result.output
        assert "not initialized" in result.output


# ===========================================================================
# switch
# ===========================================================================


class TestSwitch:
    """``db switch`` flips the active db and rejects unknown ones."""

    def test_switch_persists_current_database_to_settings_yaml(
        self, isolated_settings: Path
    ) -> None:
        """A successful switch writes ``current_database`` to settings.yaml.

        Engine-level config (including the active database) lives in
        data_dir/settings.yaml as of the 2026-06 config unification; the
        switch command persists through ``ConfigManager`` so Cortex on the
        same data_dir picks the change up on its next reload.
        """
        runner = CliRunner()
        databases_dir = isolated_settings / "databases"
        _make_db(databases_dir, "proj")

        with patch(
            "chaoscypher_cli.commands.db.switch.get_databases_dir",
            return_value=databases_dir,
        ):
            result = runner.invoke(switch, ["proj"])

        assert result.exit_code == 0, result.output
        assert "Switched to database 'proj'" in result.output
        on_disk = yaml.safe_load((isolated_settings / "settings.yaml").read_text())
        assert on_disk["current_database"] == "proj"

    def test_switch_unknown_db_errors(self, isolated_settings: Path) -> None:
        runner = CliRunner()
        databases_dir = isolated_settings / "databases"
        databases_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.db.switch.get_databases_dir",
            return_value=databases_dir,
        ):
            result = runner.invoke(switch, ["ghost"])

        assert result.exit_code == 1
        assert "Database not found" in result.output
        # Nothing persisted — no settings.yaml current_database was written.
        settings_path = isolated_settings / "settings.yaml"
        if settings_path.exists():
            on_disk = yaml.safe_load(settings_path.read_text()) or {}
            assert "current_database" not in on_disk


# ===========================================================================
# delete
# ===========================================================================


class TestDelete:
    """``db delete`` removes a db with guards and a confirm prompt."""

    def test_delete_with_yes_removes_dir(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        db_path = _make_db(databases_dir, "stale")

        with _patch_current_db("delete", "default"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["stale", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Deleted database 'stale'" in result.output
        # The directory is gone from disk.
        assert not db_path.exists()

    def test_delete_prompt_confirm_yes(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        db_path = _make_db(databases_dir, "stale")

        with _patch_current_db("delete", "default"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["stale"], input="y\n")

        assert result.exit_code == 0, result.output
        assert "permanently delete" in result.output
        assert not db_path.exists()

    def test_delete_prompt_confirm_no_aborts(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        db_path = _make_db(databases_dir, "stale")

        with _patch_current_db("delete", "default"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["stale"], input="n\n")

        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        # Cancelling leaves the database on disk.
        assert db_path.exists()

    def test_delete_default_guarded(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "default")

        with _patch_current_db("delete", "other"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["default", "--yes"])

        assert result.exit_code == 1
        assert "Cannot delete the 'default' database" in result.output
        assert (databases_dir / "default").exists()

    def test_delete_current_guarded(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "active")

        with _patch_current_db("delete", "active"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["active", "--yes"])

        assert result.exit_code == 1
        assert "Cannot delete the current database" in result.output
        assert (databases_dir / "active").exists()

    def test_delete_not_found(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        with _patch_current_db("delete", "default"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["ghost", "--yes"])

        assert result.exit_code == 1
        assert "Database not found" in result.output

    @pytest.mark.parametrize(
        "bad_name",
        ["..", "../x", "..\\x", "sub/dir", "sub\\dir", "C:\\evil", "/abs/path", "a b"],
    )
    def test_delete_rejects_invalid_names(self, tmp_path: Path, bad_name: str) -> None:
        """Names with separators/traversal never reach the filesystem."""
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "victim")

        with _patch_current_db("delete", "default"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, [bad_name, "--yes"])

        assert result.exit_code == 1
        assert "Invalid database name" in result.output
        assert (databases_dir / "victim").exists()

    @pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive filesystem guard")
    def test_delete_default_guarded_case_insensitively(self, tmp_path: Path) -> None:
        """'Default' resolves to the same dir as 'default' on Windows — guarded."""
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "default")

        with _patch_current_db("delete", "other"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["Default", "--yes"])

        assert result.exit_code == 1
        assert "Cannot delete the 'default' database" in result.output
        assert (databases_dir / "default").exists()

    @pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive filesystem guard")
    def test_delete_current_guarded_case_insensitively(self, tmp_path: Path) -> None:
        """'Active' resolves to the same dir as current 'active' on Windows — guarded."""
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "active")

        with _patch_current_db("delete", "active"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                result = runner.invoke(delete, ["Active", "--yes"])

        assert result.exit_code == 1
        assert "Cannot delete the current database" in result.output
        assert (databases_dir / "active").exists()

    def test_delete_rmtree_failure(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "stale")

        with _patch_current_db("delete", "default"):
            with patch(
                "chaoscypher_cli.commands.db.delete.get_databases_dir",
                return_value=databases_dir,
            ):
                with patch(
                    "chaoscypher_cli.commands.db.delete.shutil.rmtree",
                    side_effect=OSError("locked"),
                ):
                    result = runner.invoke(delete, ["stale", "--yes"])

        assert result.exit_code == 1
        assert "Failed to delete database" in result.output
        assert "locked" in result.output


# ===========================================================================
# info
# ===========================================================================


class TestInfo:
    """``db info`` shows stats for a db and guards not-found."""

    def _ctx_with_stats(self, nodes: int, edges: int, templates: int) -> MagicMock:
        ctx = MagicMock()
        ctx.get_stats.return_value = {
            "nodes": nodes,
            "edges": edges,
            "templates": templates,
        }
        return ctx

    def test_info_rich_with_counts(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "alpha", size=8192)
        ctx = self._ctx_with_stats(12, 7, 3)

        with patch(
            "chaoscypher_cli.commands.db.info.get_databases_dir",
            return_value=databases_dir,
        ):
            with _patch_current_db("info", "alpha"):
                with patch("chaoscypher_cli.commands.db.info.get_context", return_value=ctx):
                    result = runner.invoke(info, ["alpha"])

        assert result.exit_code == 0, result.output
        assert "alpha" in result.output
        assert "(current)" in result.output
        assert "Contents:" in result.output
        assert "12" in result.output  # nodes
        assert "Nodes:" in result.output

    def test_info_json_includes_contents(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "beta", size=4096)
        ctx = self._ctx_with_stats(5, 2, 1)

        with patch(
            "chaoscypher_cli.commands.db.info.get_databases_dir",
            return_value=databases_dir,
        ):
            with _patch_current_db("info", "default"):
                with patch("chaoscypher_cli.commands.db.info.get_context", return_value=ctx):
                    result = runner.invoke(info, ["beta", "--json"])

        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)
        assert data["name"] == "beta"
        assert data["is_current"] is False
        assert data["contents"] == {"nodes": 5, "edges": 2, "templates": 1}

    def test_info_json_not_wrapped_on_narrow_console(self, tmp_path: Path) -> None:
        """``info --json`` must parse on a narrow, no-TTY console (CI runner).

        Regression for Rich hard-wrapping the JSON to 80 cols when stdout is not
        a terminal, which split the embedded path mid-token and made the output
        unparseable. See ``test_list_json_not_wrapped_on_narrow_console``.
        """
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "beta", size=4096)
        ctx = self._ctx_with_stats(5, 2, 1)

        with patch(
            "chaoscypher_cli.commands.db.info.get_databases_dir",
            return_value=databases_dir,
        ):
            with _patch_current_db("info", "default"):
                with patch("chaoscypher_cli.commands.db.info.get_context", return_value=ctx):
                    # Force the narrow width a non-TTY CI runner sees.
                    result = runner.invoke(info, ["beta", "--json"], env={"COLUMNS": "80"})

        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)  # must not raise
        assert data["path"] == str(databases_dir / "beta")
        assert data["contents"] == {"nodes": 5, "edges": 2, "templates": 1}

    def test_info_not_found(self, tmp_path: Path) -> None:
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        databases_dir.mkdir()

        with patch(
            "chaoscypher_cli.commands.db.info.get_databases_dir",
            return_value=databases_dir,
        ):
            with _patch_current_db("info", "default"):
                with patch("chaoscypher_cli.commands.db.info.get_context") as mock_get_context:
                    result = runner.invoke(info, ["ghost"])

        assert result.exit_code == 1
        assert "Database not found" in result.output
        mock_get_context.assert_not_called()

    def test_info_connect_failure_falls_back_to_fs(self, tmp_path: Path) -> None:
        """If get_context/get_stats raises, fall back to filesystem info."""
        runner = CliRunner()
        databases_dir = tmp_path / "databases"
        _make_db(databases_dir, "alpha", size=4096)

        with patch(
            "chaoscypher_cli.commands.db.info.get_databases_dir",
            return_value=databases_dir,
        ):
            with _patch_current_db("info", "default"):
                with patch(
                    "chaoscypher_cli.commands.db.info.get_context",
                    side_effect=RuntimeError("cannot connect"),
                ):
                    result = runner.invoke(info, ["alpha"])

        assert result.exit_code == 0, result.output
        assert "alpha" in result.output
        assert "Location:" in result.output
        # No content counts rendered when the connection failed.
        assert "Contents:" not in result.output


# ===========================================================================
# path resolution
# ===========================================================================


def test_get_databases_dir_resolves_from_env_not_cli_yaml(isolated_settings, monkeypatch, tmp_path):
    """Db list/create must resolve databases under CHAOSCYPHER_DATA_DIR like
    CLIContext does. A leftover cli.yaml (retired by the 2026-06 config
    unification) must be wholly ignored — never consulted for a data dir.
    """
    import yaml as _yaml

    from chaoscypher_cli.commands.db.list import get_databases_dir

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    # A stale cli.yaml carrying a divergent data_dir — it must have no effect.
    (cfg_dir / "cli.yaml").write_text(
        _yaml.safe_dump({"paths": {"data_dir": str(tmp_path / "elsewhere")}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHAOSCYPHER_CONFIG_DIR", str(cfg_dir))

    assert get_databases_dir() == isolated_settings / "databases"


# ===========================================================================
# group registration
# ===========================================================================


def test_db_group_registers_all_subcommands() -> None:
    from chaoscypher_cli.commands.db import LAZY_SUBCOMMANDS

    for name in ("list", "create", "current", "delete", "info", "switch", "migrate"):
        assert name in LAZY_SUBCOMMANDS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
