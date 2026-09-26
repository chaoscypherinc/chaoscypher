# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``chaoscypher mount`` — pull a package, import it, serve it over MCP.

The import, indexing and serving collaborators are all mocked at the module
paths ``mount.py`` imports them from, so no real database, hub, or stdio
server is touched. What is exercised for real: argument routing (local file
vs hub reference), the derived database name, the ``mount.json`` marker
round-trip that makes a second launch skip the import, ``--refresh``,
``--no-serve``, the stderr-only output contract, and the hand-off to
``serve_stdio``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands import mount as mount_mod
from chaoscypher_cli.commands.mount import (
    MOUNT_MARKER_FILENAME,
    derive_database_name,
    is_local_package,
    mount,
    read_mount_marker,
    write_mount_marker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stats(**overrides: Any) -> MagicMock:
    """Return an ImportStats-shaped mock (success unless overridden)."""
    stats = MagicMock()
    stats.nodes_imported = 3
    stats.edges_imported = 2
    stats.sources_imported = 1
    stats.chunks_imported = 2
    stats.citations_imported = 4
    stats.imported_source_ids = ["src_1"]
    stats.imported_node_ids = ["n1", "n2", "n3"]
    stats.warnings = []
    stats.errors = []
    stats.is_success = True
    for key, value in overrides.items():
        setattr(stats, key, value)
    return stats


def _ctx(tmp_path: Path, database_name: str) -> MagicMock:
    """Return a CLIContext-shaped mock whose database_dir lives under tmp_path."""
    ctx = MagicMock()
    ctx.database_name = database_name
    ctx.database_dir = tmp_path / "databases" / database_name
    ctx.database_dir.mkdir(parents=True, exist_ok=True)
    return ctx


@pytest.fixture
def package(tmp_path: Path) -> Path:
    """A local .ccx file (contents irrelevant — the importer is mocked)."""
    pkg = tmp_path / "research-notes.ccx"
    pkg.write_bytes(b"PK\x03\x04not-really-a-package")
    return pkg


@pytest.fixture
def patched(tmp_path: Path) -> Any:
    """Patch every collaborator; yield the mocks so tests can assert on them."""
    stats = _stats()
    with (
        patch("chaoscypher_core.utils.logging.configure_logging"),
        patch.object(mount_mod, "_import_package", return_value=stats) as import_pkg,
        patch.object(mount_mod, "_index_for_search") as index,
        patch("chaoscypher_cli.context.get_context") as get_context,
        patch("chaoscypher_cli.context.reset_context") as reset_context,
        patch("chaoscypher_cli.mcp.command.serve_stdio") as serve,
    ):
        get_context.side_effect = lambda database_name, **_kw: _ctx(tmp_path, database_name)
        yield {
            "stats": stats,
            "import": import_pkg,
            "index": index,
            "get_context": get_context,
            "reset_context": reset_context,
            "serve": serve,
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestDeriveDatabaseName:
    def test_hub_reference_joins_owner_and_name(self) -> None:
        assert derive_database_name("acme/eu-ai-act") == "acme-eu-ai-act"

    def test_local_path_uses_stem(self) -> None:
        assert derive_database_name("/tmp/My Research (v2).ccx") == "My-Research-v2"

    def test_bare_name_passes_through(self) -> None:
        assert derive_database_name("medical-ontology") == "medical-ontology"

    def test_never_yields_the_reserved_default_name(self) -> None:
        # get_database_name() treats a literal "default" override as unset,
        # so an implicit mount must never land on it.
        assert derive_database_name("default.ccx") == "mounted-default"

    def test_empty_after_sanitising_falls_back(self) -> None:
        assert derive_database_name("!!!.ccx") == "mounted"


class TestIsLocalPackage:
    def test_existing_ccx_file(self, package: Path) -> None:
        assert is_local_package(str(package)) is True

    def test_hub_reference_is_not_local(self) -> None:
        assert is_local_package("acme/eu-ai-act") is False

    def test_missing_ccx_path_is_not_local(self, tmp_path: Path) -> None:
        assert is_local_package(str(tmp_path / "nope.ccx")) is False


class TestMarker:
    def test_round_trip(self, tmp_path: Path) -> None:
        write_mount_marker(
            tmp_path,
            package="acme/pkg",
            sha256="abc",
            version="1.0.0",
            archive_path=tmp_path / "acme-pkg-1.0.0.ccx",
            stats=_stats(),
        )
        marker = read_mount_marker(tmp_path)
        assert marker is not None
        assert marker["package"] == "acme/pkg"
        assert marker["sha256"] == "abc"
        assert marker["version"] == "1.0.0"
        assert marker["archive_path"] == str(tmp_path / "acme-pkg-1.0.0.ccx")
        assert marker["citations_imported"] == 4
        assert "mounted_at" in marker

    def test_missing_marker_is_none(self, tmp_path: Path) -> None:
        assert read_mount_marker(tmp_path) is None

    def test_corrupt_marker_is_none(self, tmp_path: Path) -> None:
        (tmp_path / MOUNT_MARKER_FILENAME).write_text("{not json", encoding="utf-8")
        assert read_mount_marker(tmp_path) is None

    def test_non_object_marker_is_none(self, tmp_path: Path) -> None:
        (tmp_path / MOUNT_MARKER_FILENAME).write_text("[1, 2]", encoding="utf-8")
        assert read_mount_marker(tmp_path) is None


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class TestMountLocalPackage:
    def test_imports_indexes_writes_marker_and_serves(
        self, patched: dict[str, Any], package: Path, tmp_path: Path
    ) -> None:
        result = CliRunner().invoke(mount, [str(package)])

        assert result.exit_code == 0, result.output
        patched["get_context"].assert_called_once_with(
            database_name="research-notes", explicit_database=True
        )
        patched["import"].assert_called_once()
        patched["index"].assert_called_once()
        marker = read_mount_marker(tmp_path / "databases" / "research-notes")
        assert marker is not None
        assert marker["package"] == str(package)
        assert marker["version"] is None
        patched["reset_context"].assert_called_once()
        patched["serve"].assert_called_once_with(
            database="research-notes", mode=None, server_extraction=False, explicit_database=True
        )

    def test_second_launch_skips_the_import(self, patched: dict[str, Any], package: Path) -> None:
        runner = CliRunner()
        assert runner.invoke(mount, [str(package)]).exit_code == 0
        result = runner.invoke(mount, [str(package)])

        assert result.exit_code == 0, result.output
        assert patched["import"].call_count == 1
        assert patched["serve"].call_count == 2
        assert "Already mounted" in result.output

    def test_changed_package_bytes_reimport(self, patched: dict[str, Any], package: Path) -> None:
        runner = CliRunner()
        assert runner.invoke(mount, [str(package)]).exit_code == 0
        package.write_bytes(b"PK\x03\x04a-different-package")
        result = runner.invoke(mount, [str(package)])

        assert result.exit_code == 0, result.output
        assert patched["import"].call_count == 2

    def test_refresh_forces_reimport(self, patched: dict[str, Any], package: Path) -> None:
        runner = CliRunner()
        assert runner.invoke(mount, [str(package)]).exit_code == 0
        result = runner.invoke(mount, [str(package), "--refresh"])

        assert result.exit_code == 0, result.output
        assert patched["import"].call_count == 2

    def test_no_serve_stops_after_import(self, patched: dict[str, Any], package: Path) -> None:
        result = CliRunner().invoke(mount, [str(package), "--no-serve"])

        assert result.exit_code == 0, result.output
        patched["import"].assert_called_once()
        patched["serve"].assert_not_called()
        assert "chaoscypher mcp --database research-notes" in result.output

    def test_database_and_mode_flags_are_forwarded(
        self, patched: dict[str, Any], package: Path
    ) -> None:
        result = CliRunner().invoke(mount, [str(package), "--database", "notes", "--mode", "write"])

        assert result.exit_code == 0, result.output
        patched["get_context"].assert_called_once_with(
            database_name="notes", explicit_database=True
        )
        patched["serve"].assert_called_once_with(
            database="notes", mode="write", server_extraction=False, explicit_database=True
        )

    def test_import_failure_exits_nonzero_without_serving(
        self, patched: dict[str, Any], package: Path, tmp_path: Path
    ) -> None:
        patched["import"].return_value = _stats(is_success=False, errors=["bad package"])

        result = CliRunner().invoke(mount, [str(package)])

        assert result.exit_code != 0
        assert "bad package" in result.output
        assert "Import failed" in result.output
        patched["serve"].assert_not_called()
        assert read_mount_marker(tmp_path / "databases" / "research-notes") is None

    def test_marker_is_not_written_when_indexing_fails(
        self, patched: dict[str, Any], package: Path, tmp_path: Path
    ) -> None:
        # Without the marker the next launch re-imports (an upsert) and
        # retries the indexing, so a mount done before the embedding model
        # was available is not recorded as complete.
        patched["index"].return_value = False

        result = CliRunner().invoke(mount, [str(package), "--no-serve"])

        assert result.exit_code == 0, result.output
        assert read_mount_marker(tmp_path / "databases" / "research-notes") is None

    def test_uppercase_extension_is_a_local_package(
        self, patched: dict[str, Any], tmp_path: Path
    ) -> None:
        pkg = tmp_path / "NOTES.CCX"
        pkg.write_bytes(b"PK\x03\x04x")

        result = CliRunner().invoke(mount, [str(pkg), "--no-serve"])

        assert result.exit_code == 0, result.output
        patched["get_context"].assert_called_once_with(
            database_name="NOTES", explicit_database=True
        )

    def test_missing_ccx_file_is_a_clean_error(
        self, patched: dict[str, Any], tmp_path: Path
    ) -> None:
        result = CliRunner().invoke(mount, [str(tmp_path / "missing.ccx")])

        assert result.exit_code != 0
        assert "Package file not found" in result.output
        patched["import"].assert_not_called()

    def test_all_output_goes_to_stderr(self, patched: dict[str, Any], package: Path) -> None:
        # stdout is the MCP JSON-RPC channel once serve_stdio starts; a single
        # progress line there would corrupt the handshake.
        result = CliRunner().invoke(mount, [str(package), "--no-serve"])

        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert "Mounted" in result.stderr


class TestMountHubPackage:
    def test_hub_reference_is_pulled_then_mounted(
        self, patched: dict[str, Any], tmp_path: Path
    ) -> None:
        archive = tmp_path / "acme-eu-ai-act-1.2.0.ccx"
        archive.write_bytes(b"PK\x03\x04hub-package")

        with patch.object(mount_mod, "_pull_from_hub", return_value=(archive, "1.2.0")) as pull:
            result = CliRunner().invoke(mount, ["acme/eu-ai-act", "--version", "1.2.0"])

        assert result.exit_code == 0, result.output
        pull.assert_called_once()
        assert pull.call_args.args[:2] == ("acme/eu-ai-act", "1.2.0")
        patched["get_context"].assert_called_once_with(
            database_name="acme-eu-ai-act", explicit_database=True
        )
        marker = read_mount_marker(tmp_path / "databases" / "acme-eu-ai-act")
        assert marker is not None
        assert marker["version"] == "1.2.0"
        assert marker["package"] == "acme/eu-ai-act"

    def test_pull_uses_cached_archive_for_explicit_version(self, tmp_path: Path) -> None:
        cached = tmp_path / "packages" / "acme-pkg-2.0.0.ccx"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"cached")
        console = MagicMock()

        with (
            patch(
                "chaoscypher_cli.utils.paths.get_packages_dir",
                return_value=tmp_path / "packages",
            ),
            patch("chaoscypher_cli.commands.lexicon.pull.download_package") as download,
        ):
            path, version = mount_mod._pull_from_hub("acme/pkg", "2.0.0", console)

        assert path == cached
        assert version == "2.0.0"
        download.assert_not_called()

    def test_pull_downloads_latest_through_the_shared_helper(self, tmp_path: Path) -> None:
        console = MagicMock()
        saved = tmp_path / "packages" / "pkg-3.1.0.ccx"

        with (
            patch(
                "chaoscypher_cli.utils.paths.get_packages_dir",
                return_value=tmp_path / "packages",
            ),
            patch(
                "chaoscypher_cli.commands.lexicon.pull.download_package",
                return_value=(saved, "3.1.0"),
            ) as download,
        ):
            path, version = mount_mod._pull_from_hub("pkg", None, console)

        assert (path, version) == (saved, "3.1.0")
        download.assert_called_once_with("pkg", None, tmp_path / "packages", force=True)

    def test_second_launch_of_a_hub_package_serves_offline(
        self, patched: dict[str, Any], tmp_path: Path
    ) -> None:
        """No network on relaunch: the marker's cached archive is enough."""
        archive = tmp_path / "acme-research-1.0.0.ccx"
        archive.write_bytes(b"PK\x03\x04hub-package")
        runner = CliRunner()
        with patch.object(mount_mod, "_pull_from_hub", return_value=(archive, "1.0.0")) as pull:
            assert runner.invoke(mount, ["acme/research"]).exit_code == 0
            pull.assert_called_once()

        with patch.object(mount_mod, "_pull_from_hub") as pull_again:
            result = runner.invoke(mount, ["acme/research"])

        assert result.exit_code == 0, result.output
        pull_again.assert_not_called()
        assert patched["import"].call_count == 1
        assert "Already mounted" in result.output

    def test_refresh_re_pulls_a_hub_package(self, patched: dict[str, Any], tmp_path: Path) -> None:
        archive = tmp_path / "acme-research-1.0.0.ccx"
        archive.write_bytes(b"PK\x03\x04hub-package")
        runner = CliRunner()
        with patch.object(mount_mod, "_pull_from_hub", return_value=(archive, "1.0.0")):
            assert runner.invoke(mount, ["acme/research"]).exit_code == 0
        with patch.object(mount_mod, "_pull_from_hub", return_value=(archive, "1.0.0")) as pull:
            result = runner.invoke(mount, ["acme/research", "--refresh"])

        assert result.exit_code == 0, result.output
        pull.assert_called_once()
        assert patched["import"].call_count == 2


def _async_return(value: Any) -> Any:
    """Return an AsyncMock-like awaitable returning ``value``."""
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=value)


class TestRegistration:
    def test_mount_is_registered_and_first_run_safe(self) -> None:
        from chaoscypher_cli.__main__ import _FIRST_RUN_SAFE_SUBCOMMANDS, LAZY_COMMANDS

        assert LAZY_COMMANDS["mount"][0] == "chaoscypher_cli.commands.mount:mount"
        # A fresh install with no LLM configured must still be able to mount
        # a package for an MCP host — the host's model answers the questions.
        assert "mount" in _FIRST_RUN_SAFE_SUBCOMMANDS
        from chaoscypher_cli.__main__ import _UPGRADE_SAFE_SUBCOMMANDS

        # mount targets its own database; the group-level guard must not
        # gate it on whatever the *current* database's migration state is.
        assert "mount" in _UPGRADE_SAFE_SUBCOMMANDS

    def test_marker_survives_json_round_trip_on_disk(self, tmp_path: Path) -> None:
        write_mount_marker(
            tmp_path,
            package="p",
            sha256="s",
            version=None,
            archive_path=tmp_path / "p.ccx",
            stats=_stats(),
        )
        raw = json.loads((tmp_path / MOUNT_MARKER_FILENAME).read_text(encoding="utf-8"))
        assert raw["version"] is None
