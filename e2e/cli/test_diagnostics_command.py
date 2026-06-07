# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI diagnostics command."""

from collections.abc import Callable
from pathlib import Path


class TestDiagnosticsCommand:
    """Test chaoscypher diagnostics command."""

    def test_diagnostics_export(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        tmp_path: Path,
    ) -> None:
        """Diagnostics command creates a ZIP bundle."""
        output_path = tmp_path / "diag.zip"
        result = run_cli(["diagnostics", "--output", str(output_path)], env=cli_env)
        # Command should succeed
        assert result.exit_code == 0, f"Failed: {result.output}"
        # Output file should exist and be a ZIP
        assert output_path.exists()
        with output_path.open("rb") as f:
            assert f.read(2) == b"PK", "Not a valid ZIP file"
