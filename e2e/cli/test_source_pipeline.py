# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI source management commands."""

from collections.abc import Callable
from pathlib import Path


class TestSourcePipeline:
    """Test chaoscypher source add/list/get/delete commands."""

    def test_add_text_file_index_only(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        sample_txt: Path,
    ) -> None:
        """Adding a text file with --index-only succeeds quickly."""
        result = run_cli(
            ["source", "add", str(sample_txt), "--index-only", "--quiet"],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

    def test_add_pdf_file_index_only(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        sample_pdf: Path,
    ) -> None:
        """Adding a PDF file with --index-only succeeds."""
        result = run_cli(
            ["source", "add", str(sample_pdf), "--index-only", "--quiet"],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"

    def test_list_sources(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        sample_txt: Path,
    ) -> None:
        """Listing sources shows added files."""
        run_cli(
            ["source", "add", str(sample_txt), "--index-only", "--quiet"],
            env=cli_env,
        )

        result = run_cli(["source", "list"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert "sample" in result.output.lower()

    def test_search_indexed_content(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        sample_txt: Path,
    ) -> None:
        """Searching after indexing returns results from the document."""
        run_cli(
            ["source", "add", str(sample_txt), "--index-only", "--quiet"],
            env=cli_env,
        )

        result = run_cli(["source", "search", "John Doe"], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert len(result.output.strip()) > 0
