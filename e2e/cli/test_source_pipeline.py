# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI source management commands."""

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
        """Searching after committing returns a chunk hit from the document.

        NOT a regression of the ``--index-only`` seeding this test used
        before: that flag deliberately stops the pipeline before commit,
        and chunk vectors are pushed into search's ``vec_search_chunks``
        table only from the commit phase
        (``SourceCommitService._index_chunks_to_vector_search``, called
        from both ``_commit_impl`` and ``_commit_empty`` in
        packages/core/src/chaoscypher_core/services/sources/engine/commit/service.py).
        FTS5 keyword search is populated only from extracted+committed
        *nodes* (``index_node`` / ``index_nodes_batch``) and never touches
        chunks at all. Confirmed by reading the indexing path too: CLI
        ``index_file`` (packages/cli/src/chaoscypher_cli/sources/service.py)
        never references ``search_repository``. So a source added with
        --index-only is invisible to every search mode by design, not by
        defect -- there was no production bug to report here.

        ``--skip-extract`` instead runs index + commit with zero entities,
        which takes the commit service's ``_commit_empty`` path; its own
        docstring says "Chunks are still promoted so they remain visible
        in RAG/search" -- making the document searchable without a real
        LLM. Only chunk *vectors* get indexed on this path (no nodes exist
        to keyword-index), so this uses --mode semantic explicitly:
        ``SearchRepository.vector_search`` applies no similarity
        threshold (unlike ``hybrid_search``'s 0.55 cutoff), so it
        deterministically returns the sole chunk in this fresh temp DB.
        """
        add_result = run_cli(
            ["source", "add", str(sample_txt), "--skip-extract", "--quiet", "--json"],
            env=cli_env,
        )
        assert add_result.exit_code == 0, f"Failed: {add_result.output}"
        add_payload = _parse_json_output(add_result.output)
        assert add_payload.get("status") == "completed", add_payload
        source_id = add_payload["file_id"]

        result = run_cli(
            ["source", "search", "John Doe", "--mode", "semantic", "--format", "json"],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        # search.py prints a "Searching: ..." banner before the JSON body
        # regardless of --format, so skip to the JSON array's start.
        payload = _parse_json_output(result.output)
        assert payload, f"Search returned no results:\n{result.output}"
        assert any(
            r.get("result_type") == "chunk"
            and r.get("properties", {}).get("source_id") == source_id
            for r in payload
        ), f"No chunk result from the added source ({source_id}) in: {payload}"
