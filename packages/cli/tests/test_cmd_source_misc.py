# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for source subcommands: search, list, rebuild_search, delete.

Covers:
- search: results (table/json), empty results, keyword/semantic/hybrid modes,
          --limit, exception error path, hydration of node vs chunk results,
          long label/id truncation in table display
- list: files (table), empty, status filter, pending filter, awaiting filter,
        json format, yaml format, yaml ImportError fallback, quality display,
        file sizes (B/KB/MB), date formatting, bad date fallback, error path,
        resume hint when --pending, quality_grade absent
- rebuild_search: fast path (no regeneration needed), slow path (needs regeneration),
                  result failure path
- delete: --force skips confirmation, confirm-yes proceeds, confirm-no cancels,
          file-not-found, exception error path
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.delete import delete
from chaoscypher_cli.commands.source.list import list_files
from chaoscypher_cli.commands.source.rebuild_search import rebuild_search
from chaoscypher_cli.commands.source.search import (
    _display_table,
    _hydrate_results,
    search,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_ctx(**overrides: Any) -> MagicMock:
    """Build a minimal mock CLIContext for tests."""
    ctx = MagicMock()
    ctx.database_name = "default"
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _make_file(
    fid: str = "if_src0000000001",
    filename: str = "doc.pdf",
    status: str = "indexed",
    file_size: int = 1024,
    file_type: str = "application/pdf",
    created_at: str = "2025-01-15T10:00:00",
    quality_grade: float | None = None,
    quality_label: str | None = None,
) -> dict[str, Any]:
    return {
        "id": fid,
        "filename": filename,
        "status": status,
        "file_size": file_size,
        "file_type": file_type,
        "created_at": created_at,
        "cached_quality_grade": quality_grade,
        "cached_quality_label": quality_label,
    }


# ===========================================================================
# search.py
# ===========================================================================


class TestSearchCommand:
    """Tests for the `search` Click command."""

    def _make_node(self, nid: str = "node-abc123") -> MagicMock:
        node = MagicMock()
        node.id = nid
        node.label = "Test Entity"
        node.template_id = "Person"
        node.properties = {"role": "CEO"}
        return node

    def test_keyword_search_returns_table(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        node = self._make_node()
        ctx.search_repository.keyword_search.return_value = [("node-abc123", 0.9)]
        ctx.graph_repository.get_nodes_batch.return_value = [node]

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["hello world", "--mode", "keyword"])

        assert result.exit_code == 0, result.output
        ctx.search_repository.keyword_search.assert_called_once()
        assert "Test Entity" in result.output

    def test_keyword_search_empty_results(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.keyword_search.return_value = []

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["nothing found", "--mode", "keyword"])

        assert result.exit_code == 0, result.output
        assert "No results found" in result.output

    def test_hybrid_search_returns_results(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        node = self._make_node()
        ctx.search_repository.hybrid_search = AsyncMock(return_value=[("node-abc123", 0.85)])
        ctx.graph_repository.get_nodes_batch.return_value = [node]
        ctx.embedding_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.1, 0.2, 0.3]))

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["machine learning"])

        assert result.exit_code == 0, result.output
        assert "Test Entity" in result.output

    def test_semantic_search_calls_semantic_repo(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        node = self._make_node()
        ctx.search_repository.semantic_search = AsyncMock(return_value=[("node-abc123", 0.75)])
        ctx.graph_repository.get_nodes_batch.return_value = [node]
        ctx.embedding_service.embed = AsyncMock(return_value=MagicMock(embedding=[0.1, 0.2, 0.3]))

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["neural nets", "--mode", "semantic"])

        assert result.exit_code == 0, result.output
        ctx.search_repository.semantic_search.assert_called_once()

    def test_json_output_format(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        node = self._make_node()
        ctx.search_repository.keyword_search.return_value = [("node-abc123", 0.9)]
        ctx.graph_repository.get_nodes_batch.return_value = [node]

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword", "--format", "json"])

        assert result.exit_code == 0, result.output
        assert '"score"' in result.output

    def test_limit_option_passed_to_search(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.keyword_search.return_value = []

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword", "--limit", "5"])

        assert result.exit_code == 0, result.output
        ctx.search_repository.keyword_search.assert_called_once_with("test", limit=5)

    def test_exception_causes_exit_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.keyword_search.side_effect = RuntimeError("DB down")

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword"])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_chunk_result_hydration(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        chunk_uuid = "abc123def456"
        ctx.search_repository.keyword_search.return_value = [(f"chunk:{chunk_uuid}", 0.7)]
        ctx.storage_adapter.get_chunk_by_id.return_value = {
            "content": "Some chunk content",
            "chunk_index": 0,
            "source_id": "if_src0000000001",
        }

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword"])

        assert result.exit_code == 0, result.output
        ctx.storage_adapter.get_chunk_by_id.assert_called_once_with(chunk_uuid)

    def test_chunk_result_missing_chunk_data_not_included(self) -> None:
        """If get_chunk_by_id returns None, that entry is silently skipped."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.keyword_search.return_value = [("chunk:unknown_id", 0.5)]
        ctx.storage_adapter.get_chunk_by_id.return_value = None

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword"])

        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_node_missing_from_batch_not_included(self) -> None:
        """Nodes not returned in batch are silently skipped."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.keyword_search.return_value = [("node-missing", 0.9)]
        ctx.graph_repository.get_nodes_batch.return_value = []  # empty → node not found

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword"])

        assert result.exit_code == 0
        assert "No results found" in result.output

    def test_database_option_forwarded(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.keyword_search.return_value = []

        with patch(
            "chaoscypher_cli.commands.source.search.get_context", return_value=ctx
        ) as mock_gc:
            runner.invoke(search, ["test", "--mode", "keyword", "--database", "mydb"])

        mock_gc.assert_called_once_with(database_name="mydb")

    def test_long_content_truncated_in_chunk_result(self) -> None:
        """Chunk content longer than 100 chars is truncated before display."""
        runner = CliRunner()
        ctx = _make_ctx()
        chunk_uuid = "abc123def456"
        long_content = "x" * 200
        ctx.search_repository.keyword_search.return_value = [(f"chunk:{chunk_uuid}", 0.7)]
        ctx.storage_adapter.get_chunk_by_id.return_value = {
            "content": long_content,
            "chunk_index": 1,
            "source_id": "if_src0000000001",
        }

        with patch("chaoscypher_cli.commands.source.search.get_context", return_value=ctx):
            result = runner.invoke(search, ["test", "--mode", "keyword"])

        assert result.exit_code == 0, result.output
        # Truncated content should be in results; the raw 200-char string should not appear
        assert long_content not in result.output


class TestGetEmbeddingCallbackUnit:
    """Unit test for _get_embedding_callback — exercises the inner async callback body."""

    async def test_callback_returns_embedding_vector(self) -> None:
        from chaoscypher_cli.commands.source.search import _get_embedding_callback

        ctx = _make_ctx()
        mock_embed_result = MagicMock()
        mock_embed_result.embedding = [0.1, 0.2, 0.3]

        async def fake_embed(text: str) -> MagicMock:
            return mock_embed_result

        ctx.embedding_service.embed = fake_embed

        callback = _get_embedding_callback(ctx)
        result_vec = await callback("test query")

        assert result_vec == [0.1, 0.2, 0.3]


class TestHydrateResultsUnit:
    """Unit tests for _hydrate_results helper (called directly)."""

    def test_empty_raw_results_returns_empty(self) -> None:
        ctx = _make_ctx()
        results = _hydrate_results(ctx, [])
        assert results == []

    def test_mixed_node_and_chunk(self) -> None:
        ctx = _make_ctx()
        node = MagicMock()
        node.id = "node-x"
        node.label = "Node X"
        node.template_id = "Thing"
        node.properties = {}
        ctx.graph_repository.get_nodes_batch.return_value = [node]
        ctx.storage_adapter.get_chunk_by_id.return_value = {
            "content": "short",
            "chunk_index": 0,
            "source_id": "if_src0000000001",
        }

        raw = [("node-x", 0.9), ("chunk:uuid123", 0.5)]
        results = _hydrate_results(ctx, raw)

        assert len(results) == 2
        result_types = {r["result_type"] for r in results}
        assert result_types == {"node", "chunk"}

    def test_results_sorted_by_score_descending(self) -> None:
        ctx = _make_ctx()
        node1 = MagicMock()
        node1.id = "n1"
        node1.label = "Low"
        node1.template_id = "A"
        node1.properties = {}
        node2 = MagicMock()
        node2.id = "n2"
        node2.label = "High"
        node2.template_id = "B"
        node2.properties = {}
        ctx.graph_repository.get_nodes_batch.return_value = [node1, node2]

        raw = [("n1", 0.3), ("n2", 0.9)]
        results = _hydrate_results(ctx, raw)

        assert results[0]["score"] == 0.9
        assert results[1]["score"] == 0.3


class TestDisplayTableUnit:
    """Unit tests for _display_table helper."""

    def test_long_label_truncated(self) -> None:
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        results = [
            {
                "id": "node-abc",
                "label": "A" * 60,  # > 50 chars → truncated
                "template_id": "Thing",
                "score": 0.9,
                "properties": {},
                "result_type": "node",
            }
        ]
        _display_table.__globals__["console"] = console
        _display_table(results)
        output = buf.getvalue()
        # Original 60-char label should NOT appear; truncated version with "..." should
        assert "A" * 60 not in output

    def test_long_id_truncated(self) -> None:
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        console = Console(file=buf, no_color=True, highlight=False)
        results = [
            {
                "id": "n" * 25,  # > 20 chars → truncated
                "label": "Short Label",
                "template_id": "Thing",
                "score": 0.8,
                "properties": {},
                "result_type": "node",
            }
        ]
        _display_table.__globals__["console"] = console
        _display_table(results)
        output = buf.getvalue()
        assert "n" * 25 not in output


# ===========================================================================
# list.py
# ===========================================================================


class TestListFilesCommand:
    """Tests for the `list` Click command."""

    def test_table_shows_files(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [
            _make_file("if_src0000000001", quality_grade=80.0, quality_label="A"),
        ]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "doc.pdf" in result.output
        assert "Total: 1 file(s)" in result.output

    def test_table_empty_shows_hint(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = []

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "No ingested files found" in result.output
        assert "source add" in result.output

    def test_empty_with_status_filter_shows_filter_hint(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = []

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, ["--status", "committed"])

        assert result.exit_code == 0, result.output
        assert "status=committed" in result.output

    def test_json_format(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [
            _make_file("if_src0000000001"),
        ]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, ["--format", "json"])

        assert result.exit_code == 0, result.output
        assert '"id"' in result.output
        assert "if_src0000000001" in result.output

    def test_yaml_format(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [_make_file()]

        import yaml as real_yaml

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            with patch.dict("sys.modules", {"yaml": real_yaml}):
                result = runner.invoke(list_files, ["--format", "yaml"])

        assert result.exit_code == 0, result.output

    def test_yaml_format_importerror_falls_back_to_json(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [_make_file()]

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *args, **kwargs)

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            with patch("builtins.__import__", side_effect=fake_import):
                result = runner.invoke(list_files, ["--format", "yaml"])

        assert result.exit_code == 0, result.output
        assert "PyYAML" in result.output or '"id"' in result.output

    def test_status_filter_passed_to_adapter(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = []

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            runner.invoke(list_files, ["--status", "indexed"])

        ctx.storage_adapter.list_files.assert_called_once_with(
            database_name="default", status="indexed"
        )

    def test_pending_filter_excludes_committed(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [
            _make_file("if_a", status="indexed"),
            _make_file("if_b", status="committed"),
            # Real errored status is "error" (SourceStatus.ERROR); the previous
            # fixture used a non-existent "failed" that the old buggy filter
            # matched only because it compared against the same wrong literal.
            _make_file("if_c", status="error"),
        ]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, ["--pending"])

        assert result.exit_code == 0, result.output
        assert "if_a" in result.output
        # committed and errored should not appear
        assert "if_b" not in result.output
        assert "if_c" not in result.output

    def test_pending_with_files_shows_resume_hint(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [
            _make_file("if_a", status="indexed"),
        ]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, ["--pending"])

        assert result.exit_code == 0, result.output
        assert "resume" in result.output.lower() or "cc source add" in result.output

    def test_file_size_bytes_display(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [_make_file(file_size=500)]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "500 B" in result.output

    def test_file_size_kb_display(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [_make_file(file_size=2500)]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "KB" in result.output

    def test_file_size_mb_display(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [_make_file(file_size=2_500_000)]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "MB" in result.output

    def test_bad_iso_date_falls_back_gracefully(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [
            _make_file(created_at="not-a-date"),
        ]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output

    def test_no_created_at_ok(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        f = _make_file()
        f["created_at"] = ""
        ctx.storage_adapter.list_files.return_value = [f]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output

    def test_quality_absent_shows_dash(self) -> None:
        """When cached_quality_grade is None, the column should show '-'."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [_make_file()]  # grade=None

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "-" in result.output

    def test_quality_grade_displayed(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.return_value = [
            _make_file(quality_grade=85.0, quality_label="A"),
        ]

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 0, result.output
        assert "85" in result.output

    def test_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.list_files.side_effect = RuntimeError("broken")

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, [])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_database_option_forwarded(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.database_name = "mydb"
        ctx.storage_adapter.list_files.return_value = []

        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx) as mock_gc:
            runner.invoke(list_files, ["--database", "mydb"])

        mock_gc.assert_called_once_with(database_name="mydb")


# ===========================================================================
# rebuild_search.py
# ===========================================================================


class TestRebuildSearchCommand:
    """Tests for the `rebuild-search` Click command."""

    def test_fast_path_no_regeneration(self) -> None:
        """When needs_full_reindex is False, uses rebuild_indexes (synchronous)."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.needs_full_reindex = False

        mock_search_service = MagicMock()
        mock_search_service.rebuild_indexes.return_value = {
            "success": True,
            "total_nodes": 42,
            "nodes_with_embeddings": 40,
            "chunks_indexed": 100,
        }

        with patch("chaoscypher_cli.commands.source.rebuild_search.get_context", return_value=ctx):
            with patch(
                "chaoscypher_core.services.search.engine.search.SearchService",
                return_value=mock_search_service,
            ):
                result = runner.invoke(rebuild_search, [])

        assert result.exit_code == 0, result.output
        assert "rebuilt successfully" in result.output
        assert "42" in result.output

    def test_fast_path_shows_chunks_and_nodes(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.needs_full_reindex = False

        mock_search_service = MagicMock()
        mock_search_service.rebuild_indexes.return_value = {
            "success": True,
            "total_nodes": 10,
            "nodes_with_embeddings": 8,
            "chunks_indexed": 50,
        }

        with patch("chaoscypher_cli.commands.source.rebuild_search.get_context", return_value=ctx):
            with patch(
                "chaoscypher_core.services.search.engine.search.SearchService",
                return_value=mock_search_service,
            ):
                result = runner.invoke(rebuild_search, [])

        assert "Nodes indexed: 10" in result.output
        assert "Chunks indexed: 50" in result.output

    def test_slow_path_regeneration(self) -> None:
        """When needs_full_reindex is True, uses rebuild_with_regeneration (async)."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.needs_full_reindex = True

        mock_search_service = MagicMock()
        mock_search_service.rebuild_with_regeneration = AsyncMock(
            return_value={
                "success": True,
                "total_nodes": 20,
                "nodes_with_embeddings": 18,
                "chunks_indexed": 60,
                "sources_regenerated": 3,
            }
        )
        mock_indexing_service = MagicMock()

        with patch("chaoscypher_cli.commands.source.rebuild_search.get_context", return_value=ctx):
            with patch(
                "chaoscypher_core.services.search.engine.search.SearchService",
                return_value=mock_search_service,
            ):
                with patch(
                    "chaoscypher_core.services.search.engine.index.IndexingService",
                    return_value=mock_indexing_service,
                ):
                    result = runner.invoke(rebuild_search, [])

        assert result.exit_code == 0, result.output
        assert "rebuilt successfully" in result.output
        assert "Sources re-embedded: 3" in result.output

    def test_failure_result_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.needs_full_reindex = False

        mock_search_service = MagicMock()
        mock_search_service.rebuild_indexes.return_value = {
            "success": False,
            "message": "Index build failed",
        }

        with patch("chaoscypher_cli.commands.source.rebuild_search.get_context", return_value=ctx):
            with patch(
                "chaoscypher_core.services.search.engine.search.SearchService",
                return_value=mock_search_service,
            ):
                result = runner.invoke(rebuild_search, [])

        assert result.exit_code == 1
        assert "Index build failed" in result.output

    def test_database_option_forwarded(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.search_repository.needs_full_reindex = False
        mock_search_service = MagicMock()
        mock_search_service.rebuild_indexes.return_value = {
            "success": True,
            "total_nodes": 0,
            "nodes_with_embeddings": 0,
            "chunks_indexed": 0,
        }

        with patch(
            "chaoscypher_cli.commands.source.rebuild_search.get_context", return_value=ctx
        ) as mock_gc:
            with patch(
                "chaoscypher_core.services.search.engine.search.SearchService",
                return_value=mock_search_service,
            ):
                runner.invoke(rebuild_search, ["--database", "mydb"])

        mock_gc.assert_called_once_with(database_name="mydb")

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(rebuild_search, ["--help"])
        assert result.exit_code == 0
        assert "rebuild" in result.output.lower()


# ===========================================================================
# delete.py
# ===========================================================================


class TestDeleteCommand:
    """Tests for the `delete` Click command."""

    def _make_source_record(self) -> dict[str, Any]:
        return {
            "id": "if_del0000000001",
            "filename": "to_delete.pdf",
            "status": "indexed",
        }

    def test_force_flag_skips_confirmation_and_deletes(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.return_value = self._make_source_record()

        with patch("chaoscypher_cli.commands.source.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["if_del0000000001", "--force"])

        assert result.exit_code == 0, result.output
        ctx.storage_adapter.delete_source.assert_called_once_with(
            "if_del0000000001", ctx.database_name
        )
        assert "deleted" in result.output.lower() or "✓" in result.output

    def test_confirm_yes_proceeds(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.return_value = self._make_source_record()

        with patch("chaoscypher_cli.commands.source.delete.get_context", return_value=ctx):
            # Patch Confirm.ask to return True (user said yes)
            with patch("chaoscypher_cli.commands.source.delete.Confirm.ask", return_value=True):
                result = runner.invoke(delete, ["if_del0000000001"])

        assert result.exit_code == 0, result.output
        ctx.storage_adapter.delete_source.assert_called_once()

    def test_confirm_no_cancels_without_deleting(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.return_value = self._make_source_record()

        with patch("chaoscypher_cli.commands.source.delete.get_context", return_value=ctx):
            with patch("chaoscypher_cli.commands.source.delete.Confirm.ask", return_value=False):
                result = runner.invoke(delete, ["if_del0000000001"])

        assert result.exit_code == 0, result.output
        ctx.storage_adapter.delete_source.assert_not_called()
        assert "Cancelled" in result.output

    def test_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.return_value = None

        with patch("chaoscypher_cli.commands.source.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["if_notexist"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "if_notexist" in result.output

    def test_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.side_effect = RuntimeError("DB error")

        with patch("chaoscypher_cli.commands.source.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["if_src", "--force"])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_file_info_displayed(self) -> None:
        """File ID, filename, and status are printed before confirmation."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.return_value = self._make_source_record()

        with patch("chaoscypher_cli.commands.source.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["if_del0000000001", "--force"])

        assert "to_delete.pdf" in result.output
        assert "if_del0000000001" in result.output

    def test_database_option_forwarded(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.storage_adapter.get_source.return_value = self._make_source_record()

        with patch(
            "chaoscypher_cli.commands.source.delete.get_context", return_value=ctx
        ) as mock_gc:
            runner.invoke(delete, ["if_del0000000001", "--force", "--database", "mydb"])

        mock_gc.assert_called_once_with(database_name="mydb")
