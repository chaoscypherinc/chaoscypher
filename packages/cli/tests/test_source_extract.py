# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: chaoscypher source extract --force triggers re-extraction on committed sources.

Tests the extract command end-to-end through Click's CliRunner, patching
the service layer so no real I/O or LLM calls are made.

Scenarios covered:
- Source not found exits 1 with a helpful message.
- Committed source without --force exits 1 with upgrade hint.
- Wrong status (e.g., pending) exits 1 with status message.
- Indexed source with no LLM exits 1 with config hint.
- Indexed source with LLM calls extract_entities and shows stats.
- Committed source + --force + --yes calls reset_for_re_extraction then extract_entities.
- Committed source + --force without --yes prompts and exits 0 on "N".
- --quiet flag suppresses progress output but still prints OK line.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.source.extract import extract_cmd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(
    status: str, source_id: str = "if_testsrc12345", filename: str = "doc.pdf"
) -> dict[str, Any]:
    """Return a minimal source record dict."""
    return {
        "id": source_id,
        "filename": filename,
        "status": status,
        "extraction_depth": "full",
        "forced_domain": None,
    }


def _make_llm_summary(entity_count: int = 5, rel_count: int = 3) -> dict[str, Any]:
    """Return a minimal LLM summary dict."""
    return {
        "total_calls": 2,
        "successful_calls": 2,
        "failed_calls": 0,
        "retry_calls": 0,
        "total_input_tokens": 1000,
        "total_output_tokens": 200,
        "wasted_tokens": 0,
        "estimated_cost_usd": 0.005,
        "model": "gpt-4o-mini",
        "retry_rate": 0.0,
        "success_rate": 1.0,
    }


def _make_extract_result(entity_count: int = 5, rel_count: int = 3) -> dict[str, Any]:
    """Return a minimal extract result dict."""
    return {
        "entities": [{"id": f"e{i}"} for i in range(entity_count)],
        "relationships": [{"id": f"r{i}"} for i in range(rel_count)],
        "stats": {
            "entities_count": entity_count,
            "relationships_count": rel_count,
            "groups_processed": 2,
            "groups_total": 2,
            "extraction_depth": "full",
            "detected_domain": "technical",
        },
    }


# ---------------------------------------------------------------------------
# Source-not-found
# ---------------------------------------------------------------------------


class TestSourceNotFound:
    """extract exits 1 when the source ID doesn't exist."""

    def test_exits_1_when_source_not_found(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = None  # not found
        mock_service.has_llm = False

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(extract_cmd, ["if_notexist12345"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "if_notexist12345" in result.output


# ---------------------------------------------------------------------------
# Committed without --force
# ---------------------------------------------------------------------------


class TestCommittedWithoutForce:
    """extract exits 1 with an upgrade hint when source is committed and --force is absent."""

    def test_committed_without_force_exits_1(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("committed")
        mock_service.has_llm = True

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 1
        assert "--force" in result.output

    def test_committed_without_force_does_not_call_extract(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("committed")
        mock_service.has_llm = True

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                runner.invoke(extract_cmd, ["if_testsrc12345"])

        mock_service.extract_entities.assert_not_called()
        mock_service.reset_for_re_extraction.assert_not_called()


# ---------------------------------------------------------------------------
# Wrong status
# ---------------------------------------------------------------------------


class TestWrongStatus:
    """extract exits 1 for statuses that don't allow extraction."""

    @pytest.mark.parametrize("bad_status", ["pending", "uploading", "indexing", "failed"])
    def test_wrong_status_exits_1(self, bad_status: str) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source(bad_status)
        mock_service.has_llm = True

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# No LLM
# ---------------------------------------------------------------------------


class TestNoLLM:
    """extract exits 1 when no LLM is configured."""

    def test_indexed_no_llm_exits_1(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("indexed")
        mock_service.has_llm = False

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                # check_llm_or_skip returns (proceed=False, should_skip=False) → exit 1
                with patch(
                    "chaoscypher_cli.utils.llm_check.check_llm_or_skip",
                    return_value=(False, False),
                ):
                    result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 1
        mock_service.extract_entities.assert_not_called()


# ---------------------------------------------------------------------------
# Indexed source — happy path
# ---------------------------------------------------------------------------


class TestIndexedExtraction:
    """Indexed source with LLM calls extract_entities and exits 0."""

    def test_indexed_calls_extract_entities(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("indexed")
        mock_service.has_llm = True
        mock_service.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(),
        )
        mock_service.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(extract_cmd, ["if_testsrc12345", "--quiet"])

        assert result.exit_code == 0
        mock_service.extract_entities.assert_called_once()
        mock_service.reset_for_re_extraction.assert_not_called()

    def test_indexed_quiet_output_contains_ok(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("indexed")
        mock_service.has_llm = True
        mock_service.extract_entities.return_value = (
            _make_extract_result(entity_count=7, rel_count=4),
            _make_llm_summary(),
        )
        mock_service.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(extract_cmd, ["if_testsrc12345", "--quiet"])

        assert "OK" in result.output
        assert "if_testsrc12345" in result.output

    def test_depth_flag_updates_file_record(self) -> None:
        """--depth quick is forwarded to the file record before extraction."""
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("indexed")
        mock_service.has_llm = True
        mock_service.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(),
        )
        # Simulate get_file returning a record with depth="full" (different from --depth quick)
        mock_service.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(
                    extract_cmd, ["if_testsrc12345", "--depth", "quick", "--quiet"]
                )

        assert result.exit_code == 0
        # update_file should have been called with the new depth
        mock_service.ctx.storage_adapter.update_file.assert_any_call(
            "if_testsrc12345",
            database_name=mock_service.ctx.database_name,
            updates={"extraction_depth": "quick"},
        )

    def test_domain_auto_clears_previously_forced_domain(self) -> None:
        """--domain auto actively reverts a previously-forced domain to auto-detect."""
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        src = _make_source("indexed")
        src["forced_domain"] = "legal"
        mock_service.get_file_status.return_value = src
        mock_service.has_llm = True
        mock_service.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(),
        )
        mock_service.ctx.storage_adapter.get_file.return_value = src

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(
                    extract_cmd, ["if_testsrc12345", "--domain", "auto", "--quiet"]
                )

        assert result.exit_code == 0, result.output
        mock_service.ctx.storage_adapter.update_file.assert_any_call(
            "if_testsrc12345",
            database_name=mock_service.ctx.database_name,
            updates={"forced_domain": None},
        )


# ---------------------------------------------------------------------------
# Committed + --force + --yes
# ---------------------------------------------------------------------------


class TestForceReExtract:
    """Committed source + --force + --yes calls reset then extract."""

    def test_force_yes_calls_reset_then_extract(self) -> None:
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("committed")
        mock_service.has_llm = True
        mock_service.reset_for_re_extraction.return_value = {
            "nodes_deleted": 10,
            "edges_deleted": 5,
            "templates_deleted": 2,
        }
        mock_service.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(),
        )
        mock_service.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(
                    extract_cmd, ["if_testsrc12345", "--force", "--yes", "--quiet"]
                )

        assert result.exit_code == 0
        mock_service.reset_for_re_extraction.assert_called_once_with("if_testsrc12345")
        mock_service.extract_entities.assert_called_once()

    def test_force_yes_reset_called_before_extract(self) -> None:
        """reset_for_re_extraction must precede extract_entities."""
        runner = CliRunner()

        call_order: list[str] = []

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("committed")
        mock_service.has_llm = True

        def track_reset(source_id: str) -> dict[str, int]:
            call_order.append("reset")
            return {"nodes_deleted": 3, "edges_deleted": 1, "templates_deleted": 0}

        def track_extract(source_id: str, **kwargs: Any) -> tuple[dict, dict]:
            call_order.append("extract")
            return _make_extract_result(), _make_llm_summary()

        mock_service.reset_for_re_extraction.side_effect = track_reset
        mock_service.extract_entities.side_effect = track_extract
        mock_service.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                runner.invoke(extract_cmd, ["if_testsrc12345", "--force", "--yes", "--quiet"])

        assert call_order == ["reset", "extract"], f"Unexpected call order: {call_order}"

    def test_force_without_yes_prompts_and_cancels_on_n(self) -> None:
        """--force without --yes shows a confirmation prompt; 'N' exits 0 without extracting."""
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("committed")
        mock_service.has_llm = True

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                # Simulate user typing 'n' at the prompt
                result = runner.invoke(extract_cmd, ["if_testsrc12345", "--force"], input="n\n")

        assert result.exit_code == 0  # cancelled cleanly, not an error
        mock_service.reset_for_re_extraction.assert_not_called()
        mock_service.extract_entities.assert_not_called()

    def test_force_without_yes_proceeds_on_y(self) -> None:
        """--force without --yes proceeds when user types 'y' at the prompt."""
        runner = CliRunner()

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = _make_source("committed")
        mock_service.has_llm = True
        mock_service.reset_for_re_extraction.return_value = {
            "nodes_deleted": 2,
            "edges_deleted": 1,
            "templates_deleted": 0,
        }
        mock_service.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(),
        )
        mock_service.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        mock_ctx = MagicMock()

        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(
                    extract_cmd, ["if_testsrc12345", "--force", "--quiet"], input="y\n"
                )

        assert result.exit_code == 0
        mock_service.reset_for_re_extraction.assert_called_once()
        mock_service.extract_entities.assert_called_once()


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


class TestCommandRegistration:
    """The extract_cmd is importable and has correct CLI name."""

    def test_extract_cmd_name(self) -> None:
        assert extract_cmd.name == "extract"

    def test_extract_cmd_has_force_flag(self) -> None:
        params = {p.name for p in extract_cmd.params}
        assert "force" in params

    def test_extract_cmd_has_yes_flag(self) -> None:
        params = {p.name for p in extract_cmd.params}
        assert "yes" in params

    def test_extract_cmd_has_depth_option(self) -> None:
        params = {p.name for p in extract_cmd.params}
        assert "depth" in params

    def test_extract_cmd_has_domain_option(self) -> None:
        params = {p.name for p in extract_cmd.params}
        assert "domain" in params

    def test_help_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(extract_cmd, ["--help"])
        assert result.exit_code == 0
        assert "force" in result.output.lower()


# ---------------------------------------------------------------------------
# Service method
# ---------------------------------------------------------------------------


class TestServiceResetMethod:
    """CLISourceProcessingService.reset_for_re_extraction calls the right adapters."""

    def test_reset_calls_delete_artifacts_and_storage_reset(self) -> None:
        from chaoscypher_cli.sources.service import CLISourceProcessingService

        mock_ctx = MagicMock()
        mock_ctx.database_name = "test"
        mock_ctx.storage_adapter.get_file.return_value = _make_source("committed")
        mock_ctx.graph_repository.delete_source_artifacts.return_value = {
            "nodes_deleted": 4,
            "edges_deleted": 2,
            "templates_deleted": 1,
        }

        service = CLISourceProcessingService(mock_ctx)
        removed = service.reset_for_re_extraction("if_testsrc12345")

        # delete_source_artifacts must receive the adapter's session so all
        # three SQL deletes share the adapter's transaction (real atomicity).
        mock_ctx.graph_repository.delete_source_artifacts.assert_called_once_with(
            "if_testsrc12345", session=mock_ctx.storage_adapter.session
        )
        mock_ctx.storage_adapter.reset_for_re_extraction.assert_called_once_with(
            source_id="if_testsrc12345",
            database_name="test",
        )
        assert removed["nodes_deleted"] == 4

    def test_reset_raises_if_source_not_found(self) -> None:
        from chaoscypher_cli.sources.service import CLISourceProcessingService

        mock_ctx = MagicMock()
        mock_ctx.database_name = "test"
        mock_ctx.storage_adapter.get_file.return_value = None

        service = CLISourceProcessingService(mock_ctx)

        with pytest.raises(ValueError, match="not found"):
            service.reset_for_re_extraction("if_notexist12345")


# ---------------------------------------------------------------------------
# Depth preservation (no --depth keeps the row's persisted depth)
# ---------------------------------------------------------------------------


class TestDepthPreservation:
    """Omitting --depth must not silently widen a quick source to full."""

    def _run(self, args: list[str], row_depth: str = "quick") -> tuple[Any, MagicMock]:
        runner = CliRunner()

        source = _make_source("indexed")
        source["extraction_depth"] = row_depth

        mock_service = MagicMock()
        mock_service.__enter__ = lambda s: s
        mock_service.__exit__ = MagicMock(return_value=False)
        mock_service.get_file_status.return_value = source
        mock_service.has_llm = True
        mock_service.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(),
        )
        mock_service.ctx.database_name = "test"
        mock_service.ctx.storage_adapter.get_file.return_value = source

        mock_ctx = MagicMock()
        with patch("chaoscypher_cli.context.get_context", return_value=mock_ctx):
            with patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=mock_service,
            ):
                result = runner.invoke(extract_cmd, args)
        return result, mock_service

    def test_no_depth_flag_keeps_row_depth(self) -> None:
        result, service = self._run(["if_testsrc12345"], row_depth="quick")

        assert result.exit_code == 0, result.output
        # The quick depth persisted on the row is displayed, not "full".
        assert "quick" in result.output
        # No update_file call rewrote extraction_depth.
        depth_updates = [
            c
            for c in service.ctx.storage_adapter.update_file.call_args_list
            if "extraction_depth" in (c.kwargs.get("updates") or {})
        ]
        assert depth_updates == []

    def test_explicit_depth_flag_persists(self) -> None:
        result, service = self._run(["if_testsrc12345", "--depth", "full"], row_depth="quick")

        assert result.exit_code == 0, result.output
        depth_updates = [
            c
            for c in service.ctx.storage_adapter.update_file.call_args_list
            if (c.kwargs.get("updates") or {}).get("extraction_depth") == "full"
        ]
        assert len(depth_updates) == 1
