# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI CCX package export/load commands."""

from collections.abc import Callable
from pathlib import Path


class TestCcxRoundtrip:
    """Test chaoscypher graph package export/load commands."""

    def test_load_seed_package(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
    ) -> None:
        """Loading the seed CCX package imports templates and nodes."""
        result = run_cli(
            ["graph", "package", "load", str(seed_ccx)],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

        nodes_result = run_cli(["graph", "node", "list"], env=cli_env)
        assert nodes_result.exit_code == 0
        assert "Alice Smith" in nodes_result.output or "alice" in nodes_result.output.lower()

    def test_export_package(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
        tmp_path: Path,
    ) -> None:
        """Exporting creates a valid CCX file."""
        run_cli(
            ["graph", "package", "load", str(seed_ccx)],
            env=cli_env,
        )

        export_path = tmp_path / "export.ccx"
        result = run_cli(
            ["graph", "package", "export", "-o", str(export_path)],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert export_path.exists()
        assert export_path.stat().st_size > 0

    def test_roundtrip_integrity(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
        tmp_path: Path,
    ) -> None:
        """Export then import into fresh DB preserves data."""
        run_cli(
            ["graph", "package", "load", str(seed_ccx)],
            env=cli_env,
        )

        export_path = tmp_path / "roundtrip.ccx"
        run_cli(
            ["graph", "package", "export", "-o", str(export_path)],
            env=cli_env,
        )

        run_cli(["db", "create", "roundtrip-db"], env=cli_env)
        roundtrip_env = {**cli_env, "CHAOSCYPHER_DATABASE": "roundtrip-db"}
        result = run_cli(
            ["graph", "package", "load", str(export_path)],
            env=roundtrip_env,
        )
        assert result.exit_code == 0, f"Import failed: {result.output}"

        nodes_result = run_cli(["graph", "node", "list"], env=roundtrip_env)
        assert nodes_result.exit_code == 0
        assert "Alice Smith" in nodes_result.output or "alice" in nodes_result.output.lower()
