# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional unit tests for the CLI source pipeline.

Targets the uncovered branches of ``chaoscypher_cli.sources.pipeline``:
- ``_WarningCapture`` event mapping / dedup
- duplicate-skip rendering and handling (error vs non-error status)
- ``_run_quiet`` upload/url/index/extract/commit paths, including the
  no-file-id and no-LLM branches
- ``_gate_before_extract`` (forced domain, no_confirm, no-rec, park, TTY
  prompt confirm/cancel) and ``_park`` / ``_prompt_for_domain``
- ``_run_with_ui`` stage error short-circuits and ``_print_header``
- ``_ui_upload`` / ``_ui_upload_url`` / ``_ui_index`` / ``_ui_extract`` /
  ``_ui_commit`` success + exception lines
- ``_populate_llm_metrics`` / ``_add_llm_metrics_rows`` / ``_format_quality``
  / ``_show_summary``

The pipeline is driven against a lightweight fake ``service`` (a ``MagicMock``
configured per test) so each branch can be exercised in isolation without the
heavy Core extraction/commit machinery. Real filesystem writes use ``tmp_path``.
"""

from __future__ import annotations

import logging
from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.table import Table

from chaoscypher_cli.sources.pipeline import (
    PipelineResult,
    SourcePipeline,
    _WarningCapture,
)


if TYPE_CHECKING:
    from pathlib import Path


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _capturing_console() -> tuple[Console, StringIO]:
    """Return a Rich console that writes into a StringIO buffer."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=100, no_color=True)
    return console, buf


def _make_service(*, has_llm: bool = False) -> MagicMock:
    """Build a fake CLISourceProcessingService backed by a MagicMock context."""
    service = MagicMock()
    service.has_llm = has_llm
    ctx = MagicMock()
    ctx.database_name = "test"
    ctx.storage_adapter = MagicMock()
    service.ctx = ctx
    return service


def _make_pipeline(
    service: MagicMock, *, capture: bool = True
) -> tuple[SourcePipeline, StringIO | None]:
    """Build a SourcePipeline with an optional capturing console."""
    if capture:
        console, buf = _capturing_console()
        return SourcePipeline(service, console), buf
    return SourcePipeline(service), None


# ----------------------------------------------------------------------------
# _WarningCapture
# ----------------------------------------------------------------------------


class TestWarningCapture:
    """Tests for the warning-capture logging handler."""

    def _emit(self, capture: _WarningCapture, message: str) -> None:
        record = logging.LogRecord(
            name="x",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )
        capture.emit(record)

    def test_maps_known_event_to_friendly_message(self) -> None:
        capture = _WarningCapture()
        self._emit(capture, "extraction_failed for chunk 3")
        assert capture.captured == ["Entity extraction failed for a chunk"]

    def test_unknown_event_is_ignored(self) -> None:
        capture = _WarningCapture()
        self._emit(capture, "some_internal_noise happened")
        assert capture.captured == []

    def test_deduplicates_repeated_events(self) -> None:
        capture = _WarningCapture()
        self._emit(capture, "embedding_generation_failed once")
        self._emit(capture, "embedding_generation_failed twice")
        assert capture.captured == ["Some entity embeddings failed to generate"]

    def test_parses_bracketed_structlog_event(self) -> None:
        capture = _WarningCapture()
        # structlog-style line: token after the closing bracket is the event.
        self._emit(capture, "[warning] no_loader_available file=x.bin")
        assert capture.captured == ["No loader available for this file type"]


# ----------------------------------------------------------------------------
# Duplicate-skip rendering / handling
# ----------------------------------------------------------------------------


class TestDuplicateSkip:
    """Tests for _render_duplicate_skip and _handle_duplicate_skip."""

    def test_render_non_error_status(self) -> None:
        console, buf = _capturing_console()
        SourcePipeline._render_duplicate_skip(
            {"id": "src_1", "existing_status": "committed", "filename": "a.txt"},
            console,
        )
        out = buf.getvalue()
        assert "Skipped a.txt" in out
        assert "src_1" in out
        assert "committed" in out

    def test_render_error_status_suggests_delete(self) -> None:
        console, buf = _capturing_console()
        SourcePipeline._render_duplicate_skip(
            {"id": "src_err", "existing_status": "error", "filename": "bad.txt"},
            console,
        )
        out = buf.getvalue()
        assert "chaoscypher source delete src_err" in out
        assert "status: error" in out

    def test_handle_duplicate_skip_mutates_result(self) -> None:
        service = _make_service()
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="", filename="orig.txt", success=False, status="pending")
        skip_info = {"id": "src_dup", "existing_status": "indexed", "filename": "dup.txt"}

        returned = pipeline._handle_duplicate_skip(skip_info, result)

        assert returned is result
        assert result.success is True
        assert result.status == "skipped_duplicate"
        assert result.file_id == "src_dup"
        assert result.filename == "dup.txt"
        assert result.stages_skipped == ["upload"]


# ----------------------------------------------------------------------------
# _run_quiet
# ----------------------------------------------------------------------------


class TestRunQuiet:
    """Tests for the quiet (no-UI) pipeline path."""

    def test_quiet_upload_index_no_llm_skips_extract(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello world")

        service = _make_service(has_llm=False)
        service.upload_file.return_value = "fid_1"
        service.index_file.return_value = {
            "chunks_count": 3,
            "tokens_count": 42,
            "failed_embeddings": 1,
        }
        service.commit_to_graph.return_value = {"nodes_created": 5, "edges_created": 2}

        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(
            file_path=f,
            quiet=True,
        )

        assert result.success is True
        assert result.status == "completed"
        assert result.file_id == "fid_1"
        assert result.chunks_count == 3
        assert result.tokens_count == 42
        assert result.failed_embeddings == 1
        assert "upload" in result.stages_completed
        assert "index" in result.stages_completed
        assert "extract" in result.stages_skipped  # no LLM
        assert "commit" in result.stages_completed
        assert result.nodes_created == 5
        assert result.edges_created == 2

    def test_quiet_upload_duplicate_returns_early(self, tmp_path: Path) -> None:
        f = tmp_path / "dup.txt"
        f.write_text("dup content")

        service = _make_service()
        service.upload_file.return_value = {
            "skipped_duplicate": True,
            "id": "src_existing",
            "existing_status": "committed",
            "filename": "dup.txt",
        }

        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(file_path=f, quiet=True)

        assert result.status == "skipped_duplicate"
        assert result.success is True
        assert result.file_id == "src_existing"
        # Index / commit must NOT run for a duplicate.
        service.index_file.assert_not_called()
        service.commit_to_graph.assert_not_called()

    def test_quiet_url_upload(self, tmp_path: Path) -> None:
        service = _make_service(has_llm=False)
        service.upload_url.return_value = ("fid_url", "Example Page")
        service.index_file.return_value = {"chunks_count": 1, "tokens_count": 10}

        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(
            url="https://example.com",
            skip_commit=True,
            quiet=True,
        )

        assert result.success is True
        assert result.file_id == "fid_url"
        assert result.filename == "Example Page"
        service.upload_url.assert_called_once()

    def test_quiet_url_duplicate_returns_early(self) -> None:
        service = _make_service()
        service.upload_url.return_value = (
            {
                "skipped_duplicate": True,
                "id": "src_url_dup",
                "existing_status": "indexed",
                "filename": "page.md",
            },
            "Page Title",
        )

        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(url="https://dup.example", quiet=True)

        assert result.status == "skipped_duplicate"
        assert result.file_id == "src_url_dup"

    def test_quiet_no_file_id_errors(self) -> None:
        # No file_path, no url, no file_id, and upload not in the staged path
        # because nothing to upload -> error branch.
        service = _make_service()
        pipeline, _ = _make_pipeline(service)
        # stages will include 'upload' but file_path/url are None, so file_id
        # stays None and the "No file ID" error fires.
        result = pipeline.run(quiet=True, skip_index=True, skip_extract=True, skip_commit=True)

        assert result.success is False
        assert result.status == "failed"
        assert result.error is not None
        assert "No file ID" in result.error

    def test_quiet_extract_with_llm_populates_metrics(self, tmp_path: Path) -> None:
        f = tmp_path / "e.txt"
        f.write_text("entities here")

        service = _make_service(has_llm=True)
        service.upload_file.return_value = "fid_e"
        service.index_file.return_value = {"chunks_count": 2}
        # Forced domain on the row bypasses the gate.
        service.ctx.storage_adapter.get_file.return_value = {
            "forced_domain": "technical",
            "cached_quality_grade": 88.0,
            "cached_quality_label": "Good",
        }
        service.extract_entities.return_value = (
            {
                "stats": {
                    "entities_count": 7,
                    "relationships_count": 3,
                    "detected_domain": "technical",
                }
            },
            {"total_calls": 4, "retry_calls": 1, "model": "test-model"},
        )

        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(
            file_path=f,
            skip_commit=True,
            no_confirm=True,
            quiet=True,
        )

        assert result.success is True
        assert "extract" in result.stages_completed
        assert result.entities_count == 7
        assert result.relationships_count == 3
        assert result.detected_domain == "technical"
        assert result.llm_total_calls == 4
        assert result.llm_retry_calls == 1
        assert result.llm_model == "test-model"
        assert result.quality_grade == 88.0
        assert result.quality_label == "Good"


# ----------------------------------------------------------------------------
# _gate_before_extract / _park / _prompt_for_domain
# ----------------------------------------------------------------------------


class TestGateBeforeExtract:
    """Tests for the domain-confirmation gate."""

    def test_no_llm_is_noop_proceeds(self) -> None:
        service = _make_service(has_llm=False)
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="f", filename="f", success=False, status="pending")
        assert pipeline._gate_before_extract("f", result, no_confirm=False) is True

    def test_forced_domain_bypasses_gate(self) -> None:
        service = _make_service(has_llm=True)
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": "legal"}
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="f", filename="f", success=False, status="pending")
        assert pipeline._gate_before_extract("f", result, no_confirm=False) is True
        service.detect_domain_for_source.assert_not_called()

    def test_no_confirm_records_recommendation(self) -> None:
        service = _make_service(has_llm=True)
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        service.detect_domain_for_source.return_value = {
            "detected_domain": "medical",
            "confidence": 0.9,
            "ranking": [{"domain": "medical", "score": 9.0}],
            "low_confidence": False,
        }
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="f", filename="f", success=False, status="pending")

        assert pipeline._gate_before_extract("f", result, no_confirm=True) is True
        assert result.detected_domain == "medical"
        assert result.detection_confidence == 0.9
        assert result.detection_low_confidence is False

    def test_no_rec_proceeds(self) -> None:
        service = _make_service(has_llm=True)
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        service.detect_domain_for_source.return_value = None
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="f", filename="f", success=False, status="pending")

        assert pipeline._gate_before_extract("f", result, no_confirm=False) is True

    def test_non_tty_parks(self) -> None:
        service = _make_service(has_llm=True)
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        rec = {
            "detected_domain": "news",
            "confidence": 0.55,
            "ranking": [{"domain": "news", "score": 5.5}],
            "low_confidence": True,
        }
        service.detect_domain_for_source.return_value = rec
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="fid", filename="f", success=False, status="pending")

        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stderr.isatty", return_value=False),
            patch(
                "chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"
            ) as mock_park,
        ):
            proceed = pipeline._gate_before_extract("fid", result, no_confirm=False)

        assert proceed is False
        assert result.parked_for_confirmation is True
        assert result.status == "awaiting_confirmation"
        assert "cc source confirm fid" in result.error
        mock_park.assert_called_once()

    def test_tty_prompt_confirms_and_persists(self) -> None:
        service = _make_service(has_llm=True)
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        service.detect_domain_for_source.return_value = {
            "detected_domain": "financial",
            "confidence": 0.8,
            "ranking": [{"domain": "financial", "score": 8.0}],
            "low_confidence": False,
        }
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="fid", filename="f", success=False, status="pending")

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stderr.isatty", return_value=True),
            patch.object(pipeline, "_prompt_for_domain", return_value="legal"),
        ):
            proceed = pipeline._gate_before_extract("fid", result, no_confirm=False)

        assert proceed is True
        assert result.detected_domain == "legal"
        service.ctx.storage_adapter.update_file.assert_called_once()
        _, kwargs = service.ctx.storage_adapter.update_file.call_args
        assert kwargs["updates"]["forced_domain"] == "legal"
        assert kwargs["updates"]["confirmation_required"] is False

    def test_tty_prompt_cancel_sets_cancelled(self) -> None:
        service = _make_service(has_llm=True)
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        service.detect_domain_for_source.return_value = {
            "detected_domain": "generic",
            "confidence": 0.7,
            "ranking": [],
            "low_confidence": False,
        }
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="fid", filename="f", success=False, status="pending")

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stderr.isatty", return_value=True),
            patch.object(pipeline, "_prompt_for_domain", return_value=None),
        ):
            proceed = pipeline._gate_before_extract("fid", result, no_confirm=False)

        assert proceed is False
        assert result.status == "cancelled"
        assert result.error == "Cancelled at domain confirmation"

    def test_quiet_gate_parks_returns_early(self) -> None:
        # Covers _run_quiet's `return result` when the gate stops extraction.
        service = _make_service(has_llm=True)
        service.upload_file.return_value = "fid_q"
        service.index_file.return_value = {"chunks_count": 1}
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        service.detect_domain_for_source.return_value = {
            "detected_domain": "news",
            "confidence": 0.5,
            "ranking": [],
            "low_confidence": True,
        }
        pipeline, _ = _make_pipeline(service)
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stderr.isatty", return_value=False),
            patch("chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"),
        ):
            result = pipeline.run(
                file_id="fid_q",
                skip_index=True,
                skip_commit=True,
                quiet=True,
            )
        assert result.status == "awaiting_confirmation"
        service.extract_entities.assert_not_called()

    def test_park_quiet_does_not_print(self) -> None:
        service = _make_service(has_llm=True)
        pipeline, buf = _make_pipeline(service)
        result = PipelineResult(file_id="fid", filename="f", success=False, status="pending")
        rec = {
            "detected_domain": "news",
            "confidence": 0.5,
            "ranking": [],
            "low_confidence": True,
        }
        with patch("chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"):
            pipeline._park("fid", rec, result, quiet=True)

        assert result.parked_for_confirmation is True
        assert buf is not None
        assert "AWAITING" not in buf.getvalue()

    def test_park_non_quiet_prints_awaiting(self) -> None:
        service = _make_service(has_llm=True)
        pipeline, buf = _make_pipeline(service)
        result = PipelineResult(file_id="fid", filename="f", success=False, status="pending")
        rec = {
            "detected_domain": "news",
            "confidence": 0.5,
            "ranking": [],
            "low_confidence": True,
        }
        with patch("chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"):
            pipeline._park("fid", rec, result, quiet=False)

        assert buf is not None
        assert "AWAITING" in buf.getvalue()
        assert "fid" in buf.getvalue()


class TestPromptForDomain:
    """Tests for the interactive TTY domain prompt."""

    def test_prompt_returns_choice(self) -> None:
        service = _make_service(has_llm=True)
        pipeline, _ = _make_pipeline(service)
        rec = {
            "detected_domain": "technical",
            "confidence": 0.9,
            "ranking": [{"domain": "technical", "score": 9.0}],
            "low_confidence": False,
        }
        with patch("rich.prompt.Prompt.ask", return_value="technical"):
            assert pipeline._prompt_for_domain(rec) == "technical"

    def test_prompt_cancel_returns_none(self) -> None:
        service = _make_service(has_llm=True)
        pipeline, _ = _make_pipeline(service)
        rec = {
            "detected_domain": "generic",
            "confidence": 0.3,
            "ranking": [],
            "low_confidence": True,
        }
        with patch("rich.prompt.Prompt.ask", return_value="cancel"):
            assert pipeline._prompt_for_domain(rec) is None


# ----------------------------------------------------------------------------
# _run_with_ui and the per-stage UI helpers
# ----------------------------------------------------------------------------


class TestRunWithUI:
    """Tests for the spinner-per-stage UI pipeline path."""

    def test_full_ui_run_success(self, tmp_path: Path) -> None:
        f = tmp_path / "ui.txt"
        f.write_text("ui content")

        service = _make_service(has_llm=False)
        service.upload_file.return_value = "fid_ui"
        service.get_file_status.return_value = {"status": "uploaded"}
        service.index_file.return_value = {
            "chunks_count": 4,
            "tokens_count": 100,
            "failed_embeddings": 0,
        }
        service.commit_to_graph.return_value = {"nodes_created": 3, "edges_created": 1}

        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(file_path=f, quiet=False)

        assert result.success is True
        assert "upload" in result.stages_completed
        assert "index" in result.stages_completed
        assert "extract" in result.stages_skipped
        assert "commit" in result.stages_completed
        assert buf is not None
        out = buf.getvalue()
        assert "Source Pipeline" in out
        assert "Upload" in out
        assert "Index" in out

    def test_ui_upload_duplicate_short_circuits(self, tmp_path: Path) -> None:
        f = tmp_path / "d.txt"
        f.write_text("dup")
        service = _make_service()
        service.upload_file.return_value = {
            "skipped_duplicate": True,
            "id": "src_dup_ui",
            "existing_status": "committed",
            "filename": "d.txt",
        }
        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(file_path=f, quiet=False)

        assert result.status == "skipped_duplicate"
        service.index_file.assert_not_called()

    def test_ui_upload_error_short_circuits(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        service = _make_service()
        service.upload_file.side_effect = RuntimeError("disk full")
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(file_path=f, quiet=False)

        assert result.success is False
        assert result.error == "disk full"
        assert buf is not None
        assert "Upload" in buf.getvalue()
        service.index_file.assert_not_called()

    def test_ui_no_file_id_error(self) -> None:
        # Resume with file_id=None but skip upload (no file_path) -> "No file ID".
        service = _make_service()
        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(skip_index=True, skip_extract=True, skip_commit=True, quiet=False)
        assert result.error == "No file ID"
        assert result.success is False

    def test_ui_index_cached_skips_indexing(self) -> None:
        service = _make_service()
        service.get_file_status.return_value = {"status": "indexed"}
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_cached",
            skip_extract=True,
            skip_commit=True,
            quiet=False,
        )
        assert "index" in result.stages_completed
        service.index_file.assert_not_called()
        assert buf is not None
        assert "cached" in buf.getvalue()

    def test_ui_index_shows_failed_embeddings_detail(self) -> None:
        # Covers the failed-embeddings detail branch in _ui_index.
        service = _make_service()
        service.get_file_status.return_value = {"status": "uploaded"}
        service.index_file.return_value = {
            "chunks_count": 5,
            "tokens_count": 1500,
            "failed_embeddings": 2,
        }
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_fe",
            skip_extract=True,
            skip_commit=True,
            quiet=False,
        )
        assert result.failed_embeddings == 2
        assert buf is not None
        out = buf.getvalue()
        assert "2 embeddings failed" in out
        assert "1,500 tokens" in out

    def test_ui_index_error_short_circuits(self) -> None:
        service = _make_service()
        service.get_file_status.return_value = {"status": "uploaded"}
        service.index_file.side_effect = ValueError("bad chunks")
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_idx",
            skip_extract=True,
            skip_commit=True,
            quiet=False,
        )
        assert result.error == "bad chunks"
        assert result.success is False

    def test_ui_extract_no_llm_skips(self) -> None:
        service = _make_service(has_llm=False)
        service.get_file_status.return_value = {"status": "indexed"}
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_ex",
            skip_index=True,
            skip_commit=True,
            quiet=False,
        )
        assert "extract" in result.stages_skipped
        assert buf is not None
        assert "no LLM configured" in buf.getvalue()

    def test_ui_extract_success_with_domain_callback(self) -> None:
        service = _make_service(has_llm=True)
        service.get_file_status.return_value = {"status": "indexed"}
        # Forced domain to bypass the gate quickly.
        service.ctx.storage_adapter.get_file.return_value = {
            "forced_domain": "technical",
            "cached_quality_grade": 75.0,
            "cached_quality_label": "Good",
        }

        def _extract(file_id, progress_callback=None, domain_callback=None, filtering_mode=None):
            if domain_callback:
                domain_callback("technical")
            if progress_callback:
                progress_callback(1, 2)
                progress_callback(2, 2)
            return (
                {
                    "stats": {
                        "entities_count": 9,
                        "relationships_count": 4,
                        "groups_processed": 2,
                    }
                },
                {"total_calls": 6, "retry_calls": 2, "model": "ollama/x"},
            )

        service.extract_entities.side_effect = _extract

        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_ex2",
            skip_index=True,
            skip_commit=True,
            no_confirm=False,
            quiet=False,
        )

        assert "extract" in result.stages_completed
        assert result.entities_count == 9
        assert result.relationships_count == 4
        assert result.detected_domain == "technical"
        assert result.llm_retry_calls == 2
        assert result.quality_grade == 75.0
        assert buf is not None
        out = buf.getvalue()
        assert "Domain:" in out
        assert "Extract" in out

    def test_ui_extract_domain_from_stats_when_no_callback(self) -> None:
        # Covers the `if not result.detected_domain` fallback in _ui_extract,
        # i.e. domain_callback never fired so the domain comes from stats.
        service = _make_service(has_llm=True)
        service.get_file_status.return_value = {"status": "indexed"}
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": "scientific"}

        def _extract(file_id, progress_callback=None, domain_callback=None, filtering_mode=None):
            # Deliberately do NOT call domain_callback.
            return (
                {"stats": {"entities_count": 2, "detected_domain": "scientific"}},
                {"total_calls": 1},
            )

        service.extract_entities.side_effect = _extract
        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_dstats",
            skip_index=True,
            skip_commit=True,
            quiet=False,
        )
        assert result.detected_domain == "scientific"

    def test_ui_extract_error_short_circuits(self) -> None:
        service = _make_service(has_llm=True)
        service.get_file_status.return_value = {"status": "indexed"}
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": "technical"}
        service.extract_entities.side_effect = RuntimeError("llm blew up")
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_ex3",
            skip_index=True,
            skip_commit=True,
            quiet=False,
        )
        assert result.error == "llm blew up"
        assert result.success is False
        assert buf is not None
        assert "Extract" in buf.getvalue()

    def test_ui_commit_success(self) -> None:
        service = _make_service(has_llm=False)
        service.get_file_status.return_value = {"status": "indexed"}
        service.commit_to_graph.return_value = {"nodes_created": 10, "edges_created": 6}
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_c",
            skip_index=True,
            skip_extract=True,
            quiet=False,
        )
        assert "commit" in result.stages_completed
        assert result.nodes_created == 10
        assert result.edges_created == 6
        assert buf is not None
        out = buf.getvalue()
        assert "Commit" in out
        assert "10 nodes" in out

    def test_ui_commit_error_short_circuits(self) -> None:
        service = _make_service(has_llm=False)
        service.get_file_status.return_value = {"status": "indexed"}
        service.commit_to_graph.side_effect = ValueError("commit failed")
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            file_id="fid_c2",
            skip_index=True,
            skip_extract=True,
            quiet=False,
        )
        assert result.error == "commit failed"
        assert result.success is False

    def test_ui_gate_parks_short_circuits(self) -> None:
        service = _make_service(has_llm=True)
        service.get_file_status.return_value = {"status": "indexed"}
        service.ctx.storage_adapter.get_file.return_value = {"forced_domain": None}
        service.detect_domain_for_source.return_value = {
            "detected_domain": "news",
            "confidence": 0.5,
            "ranking": [],
            "low_confidence": True,
        }
        pipeline, buf = _make_pipeline(service)
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stderr.isatty", return_value=False),
            patch("chaoscypher_core.operations.importing.confirmation_gate.park_for_confirmation"),
        ):
            result = pipeline.run(
                file_id="fid_gate",
                skip_index=True,
                skip_commit=True,
                quiet=False,
            )
        assert result.status == "awaiting_confirmation"
        # extract_entities must not have been called after a park.
        service.extract_entities.assert_not_called()


class TestUIUploadURL:
    """Tests for the URL-upload UI helper paths."""

    def test_ui_upload_url_success(self) -> None:
        service = _make_service(has_llm=False)
        service.upload_url.return_value = ("fid_u", "My Page")
        service.get_file_status.return_value = {"status": "uploaded"}
        service.index_file.return_value = {"chunks_count": 1, "tokens_count": 5}
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(
            url="https://example.org",
            skip_extract=True,
            skip_commit=True,
            quiet=False,
        )
        assert result.success is True
        assert result.file_id == "fid_u"
        assert buf is not None
        out = buf.getvalue()
        assert "Fetch URL" in out
        assert "My Page" in out

    def test_ui_upload_url_error(self) -> None:
        service = _make_service()
        service.upload_url.side_effect = ValueError("404 not found")
        pipeline, buf = _make_pipeline(service)
        result = pipeline.run(url="https://bad.example", quiet=False)
        assert result.success is False
        assert result.error == "404 not found"
        assert buf is not None
        assert "Fetch URL" in buf.getvalue()

    def test_ui_upload_url_duplicate(self) -> None:
        service = _make_service()
        service.upload_url.return_value = (
            {
                "skipped_duplicate": True,
                "id": "src_url_d",
                "existing_status": "committed",
                "filename": "p.md",
            },
            "Title",
        )
        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(url="https://dup.example", quiet=False)
        assert result.status == "skipped_duplicate"


# ----------------------------------------------------------------------------
# _print_header
# ----------------------------------------------------------------------------


class TestPrintHeader:
    """Tests for the header panel rendering."""

    def test_header_with_url(self) -> None:
        service = _make_service()
        pipeline, buf = _make_pipeline(service)
        pipeline._print_header(None, None, "full", url="https://example.com/page")
        assert buf is not None
        assert "https://example.com/page" in buf.getvalue()

    def test_header_with_file_path_and_depth(self, tmp_path: Path) -> None:
        service = _make_service()
        pipeline, buf = _make_pipeline(service)
        f = tmp_path / "report.pdf"
        pipeline._print_header(f, None, "quick")
        assert buf is not None
        out = buf.getvalue()
        assert "report.pdf" in out
        assert "Depth: quick" in out

    def test_header_file_id_only(self) -> None:
        service = _make_service()
        pipeline, buf = _make_pipeline(service)
        pipeline._print_header(None, "fid_123", "full")
        assert buf is not None
        assert "file fid_123" in buf.getvalue()

    def test_header_truncates_long_filename(self) -> None:
        service = _make_service()
        console = Console(file=StringIO(), force_terminal=False, width=40, no_color=True)
        pipeline = SourcePipeline(service, console)
        long_url = "https://example.com/" + "a" * 200
        # Should not raise; truncation path executes.
        pipeline._print_header(None, None, "full", url=long_url)


# ----------------------------------------------------------------------------
# LLM metrics / quality / summary
# ----------------------------------------------------------------------------


class TestMetricsAndSummary:
    """Tests for metric population and summary rendering helpers."""

    def test_populate_llm_metrics(self) -> None:
        service = _make_service()
        pipeline, _ = _make_pipeline(service)
        result = PipelineResult(file_id="f", filename="f", success=True, status="completed")
        pipeline._populate_llm_metrics(
            result,
            {
                "total_calls": 10,
                "successful_calls": 9,
                "failed_calls": 1,
                "retry_calls": 2,
                "total_input_tokens": 1000,
                "total_output_tokens": 500,
                "wasted_tokens": 50,
                "estimated_cost_usd": 0.1234,
                "model": "gpt-test",
                "retry_rate": 0.2,
                "success_rate": 0.9,
            },
        )
        assert result.llm_total_calls == 10
        assert result.llm_successful_calls == 9
        assert result.llm_failed_calls == 1
        assert result.llm_retry_calls == 2
        assert result.llm_total_input_tokens == 1000
        assert result.llm_total_output_tokens == 500
        assert result.llm_wasted_tokens == 50
        assert result.llm_estimated_cost_usd == 0.1234
        assert result.llm_model == "gpt-test"
        assert result.extraction_mode == "internal"
        assert result.llm_retry_rate == 0.2
        assert result.llm_success_rate == 0.9

    def test_add_llm_metrics_rows_with_cost_and_waste(self) -> None:
        result = PipelineResult(file_id="f", filename="f", success=True, status="completed")
        result.llm_total_calls = 8
        result.llm_successful_calls = 7
        result.llm_retry_calls = 3
        result.llm_total_input_tokens = 800
        result.llm_total_output_tokens = 200
        result.llm_wasted_tokens = 100
        result.llm_estimated_cost_usd = 0.5
        result.llm_model = "openai/gpt-4"
        result.extraction_mode = "internal"

        table = Table()
        table.add_column("k")
        table.add_column("v")
        SourcePipeline._add_llm_metrics_rows(table, result)
        # Render to text to assert the rows landed.
        buf = StringIO()
        Console(file=buf, no_color=True, width=80).print(table)
        out = buf.getvalue()
        assert "Calls" in out
        assert "3 retries" in out
        assert "wasted" in out
        assert "$0.5000" in out
        assert "INTERNAL" in out

    def test_add_llm_metrics_rows_sub_cent_and_local(self) -> None:
        result = PipelineResult(file_id="f", filename="f", success=True, status="completed")
        result.llm_total_calls = 1
        result.llm_estimated_cost_usd = 0.0
        result.llm_model = "ollama/llama3"
        table = Table()
        table.add_column("k")
        table.add_column("v")
        SourcePipeline._add_llm_metrics_rows(table, result)
        buf = StringIO()
        Console(file=buf, no_color=True, width=80).print(table)
        assert "$0.00 (local)" in buf.getvalue()

    def test_add_llm_metrics_rows_sub_cent_cost(self) -> None:
        # Covers the `cost_str = "<$0.01"` branch (0 < cost < 0.01).
        result = PipelineResult(file_id="f", filename="f", success=True, status="completed")
        result.llm_total_calls = 2
        result.llm_estimated_cost_usd = 0.0005
        result.llm_model = "openai/gpt-4"
        table = Table()
        table.add_column("k")
        table.add_column("v")
        SourcePipeline._add_llm_metrics_rows(table, result)
        buf = StringIO()
        Console(file=buf, no_color=True, width=80).print(table)
        assert "<$0.01" in buf.getvalue()

    def test_format_quality_colors(self) -> None:
        # High grade -> green, label preserved.
        assert "70/100" in SourcePipeline._format_quality(70.0, "Good")
        assert "Good" in SourcePipeline._format_quality(70.0, "Good")
        # No label -> "Unknown".
        assert "Unknown" in SourcePipeline._format_quality(20.0, None)

    def test_show_summary_full(self) -> None:
        service = _make_service()
        pipeline, buf = _make_pipeline(service)
        result = PipelineResult(
            file_id="fid_sum",
            filename="x.txt",
            success=True,
            status="completed",
        )
        result.detected_domain = "technical"
        result.chunks_count = 5
        result.entities_count = 12
        result.relationships_count = 6
        result.quality_grade = 81.0
        result.quality_label = "Good"
        result.nodes_created = 8
        result.edges_created = 3
        result.duration_seconds = 2.5
        result.llm_total_calls = 4
        result.stages_skipped = ["extract"]
        result.warnings = ["First warning", "Second warning"]

        pipeline._show_summary(result)
        assert buf is not None
        out = buf.getvalue()
        assert "fid_sum" in out
        assert "technical" in out
        assert "Complete" in out
        assert "First warning" in out
        assert "Second warning" in out
        assert "extract" in out  # skipped row

    def test_show_summary_failed_with_error(self) -> None:
        service = _make_service()
        pipeline, buf = _make_pipeline(service)
        result = PipelineResult(
            file_id="fid_fail",
            filename="x.txt",
            success=False,
            status="failed",
        )
        result.error = "Something broke"
        result.duration_seconds = 1.0
        pipeline._show_summary(result)
        assert buf is not None
        out = buf.getvalue()
        assert "Failed" in out
        assert "Something broke" in out


# ----------------------------------------------------------------------------
# run() outer exception handling
# ----------------------------------------------------------------------------


class TestRunExceptionHandling:
    """Tests for the run() outer try/except wrapper."""

    def test_run_catches_unexpected_exception(self, tmp_path: Path) -> None:
        f = tmp_path / "boom.txt"
        f.write_text("boom")
        service = _make_service()
        # Force an exception inside the staged path by making upload raise a
        # type that the UI helpers don't catch (they catch Exception, so to hit
        # the outer handler use the quiet path where upload is wrapped only by
        # run()). _run_quiet does not wrap upload in try/except.
        service.upload_file.side_effect = RuntimeError("catastrophic")
        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(file_path=f, quiet=True)
        assert result.success is False
        assert result.status == "failed"
        assert result.error == "catastrophic"

    def test_run_marks_failed_when_error_set(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("data")
        service = _make_service(has_llm=False)
        service.upload_file.return_value = "fid_ok"
        service.index_file.return_value = {"chunks_count": 1}
        service.commit_to_graph.return_value = {"nodes_created": 1}
        pipeline, _ = _make_pipeline(service)
        result = pipeline.run(file_path=f, quiet=True)
        assert result.success is True
        assert result.status == "completed"


@pytest.mark.parametrize(
    ("step", "total", "icon", "detail", "elapsed"),
    [
        (1, 4, "✓", "", None),
        (2, 4, "✗", "boom", 1.23),
        (3, 4, "-", "skipped", None),
    ],
)
def test_format_stage_line(
    step: int, total: int, icon: str, detail: str, elapsed: float | None
) -> None:
    """_format_stage_line renders step counter, icon, label and optional detail."""
    service = _make_service()
    pipeline, _ = _make_pipeline(service)
    line = pipeline._format_stage_line(step, total, icon, "green", "Upload", detail, elapsed)
    assert f"[{step}/{total}]" in line
    assert "Upload" in line
    assert icon in line
    if detail:
        assert detail in line
    if elapsed is not None:
        assert f"{elapsed:.1f}s" in line
