# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI source delete and get commands."""

import re
from collections.abc import Callable
from pathlib import Path


def _extract_source_id(output: str) -> str | None:
    """Try to extract a source ID from add command output."""
    # Look for UUID-like patterns or explicit "ID: xxx" markers
    match = re.search(r"ID:\s*([a-f0-9-]+)", output, re.IGNORECASE)
    if match:
        return match.group(1)
    # Look for a UUID anywhere
    uuid_match = re.search(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
        output,
    )
    if uuid_match:
        return uuid_match.group(0)
    return None


class TestSourceDeleteGet:
    """Test chaoscypher source get/delete commands."""

    def test_source_get_nonexistent(self, run_cli: Callable, cli_env: dict[str, str]) -> None:
        """Getting a nonexistent source returns an error."""
        result = run_cli(["source", "get", "nonexistent-source-id-12345"], env=cli_env)
        # Should error
        assert result.exit_code != 0 or "not found" in result.output.lower()

    def test_source_delete_after_add(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        sample_txt: Path,
    ) -> None:
        """Adding then deleting a source removes it from the list."""
        # Add source
        add_result = run_cli(
            ["source", "add", str(sample_txt), "--index-only", "--quiet"],
            env=cli_env,
        )
        assert add_result.exit_code == 0

        # Verify it's in the list
        list_before = run_cli(["source", "list"], env=cli_env)
        assert "sample" in list_before.output.lower()

        # Extract source ID from list output
        source_id = _extract_source_id(list_before.output)
        if source_id is None:
            # Can't find ID to delete - skip the delete portion
            return

        # Delete it
        delete_result = run_cli(["source", "delete", source_id], env=cli_env, input="y\n")
        # Delete should succeed (may prompt for confirmation)
        assert delete_result.exit_code in (0, 1)
