# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI source delete and get commands."""

import json
from collections.abc import Callable
from pathlib import Path


def _parse_json_output(text: str) -> object:
    """Parse the JSON document out of noisy CLI stdout.

    Every CLI invocation against ``cli_env`` logs Alembic INFO lines to
    stdout ahead of the actual ``--json``/``--format json`` payload --
    ``cli_env``'s ``LOG_LEVEL=WARNING`` does not suppress them, since
    Alembic configures its own stdout logging independent of the app's
    level. Reproduced live (not assumed): output looks like
    ``INFO  [alembic.runtime.migration] Context impl SQLiteImpl. ...``
    followed by the JSON body. A naive "skip to the first '{' or '['"
    breaks here too: the Alembic line itself contains a bracketed
    ``[alembic.runtime.migration]`` logger name that sorts before the
    real payload. Instead, try each ``{``/``[`` position left to right
    and return the first one whose remainder parses as JSON -- the noisy
    log line isn't valid JSON on its own, so it's skipped.
    """
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            return json.loads(text[i:])
        except json.JSONDecodeError:
            continue
    msg = f"no JSON document found in output: {text!r}"
    raise AssertionError(msg)


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
        """Adding then deleting a source removes it from the list.

        ``source list``'s table truncates the ID column (Rich renders
        e.g. ``3ec83ec7-29…`` once the row is wide), which made the old
        regex-based extraction from that table fail on every run and hit
        an early ``return`` before ``source delete`` was ever invoked.
        ``source add --json`` prints the untruncated ``file_id`` directly
        (``_result_to_dict`` in
        packages/cli/src/chaoscypher_cli/commands/source/add.py), so pull
        the ID from there instead of parsing table output.
        """
        # Add source, capturing the full ID via --json (no truncation).
        add_result = run_cli(
            ["source", "add", str(sample_txt), "--index-only", "--quiet", "--json"],
            env=cli_env,
        )
        assert add_result.exit_code == 0, f"Failed: {add_result.output}"
        add_payload = _parse_json_output(add_result.output)
        source_id = add_payload.get("file_id")
        assert source_id, f"No file_id in add output: {add_payload}"

        # Verify it's in the list.
        list_before = run_cli(["source", "list"], env=cli_env)
        assert "sample" in list_before.output.lower()

        # Delete it. --force skips the interactive confirmation prompt so
        # the outcome doesn't depend on CliRunner's stdin-piping behavior.
        delete_result = run_cli(["source", "delete", source_id, "--force"], env=cli_env)
        assert delete_result.exit_code == 0, f"Delete failed: {delete_result.output}"

        # Re-list and confirm the source is actually gone.
        list_after = run_cli(["source", "list", "--format", "json"], env=cli_env)
        assert list_after.exit_code == 0, f"Failed: {list_after.output}"
        remaining_ids = {f["id"] for f in _parse_json_output(list_after.output)}
        assert source_id not in remaining_ids, (
            f"{source_id} still present after delete: {remaining_ids}"
        )
