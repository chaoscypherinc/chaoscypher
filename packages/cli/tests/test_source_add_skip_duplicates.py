# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: chaoscypher source add --skip-duplicates surfaces existing-source guidance.

Tests the duplicate-detection path end-to-end through the CLI pipeline:
- When service.upload_file returns a skipped_duplicate dict, the pipeline
  calls _render_duplicate_skip and exits early (no index / extract / commit).
- Error-state siblings suggest 'chaoscypher source delete <id>' in output.
- Non-error duplicates print a dim informational line.
- The pipeline result has success=True and status='skipped_duplicate'.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

from rich.console import Console

from chaoscypher_cli.sources.pipeline import PipelineResult, SourcePipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skip_info(
    source_id: str = "if_existingabc123",
    existing_status: str = "error",
    filename: str = "doc.txt",
) -> dict[str, Any]:
    """Build a minimal skipped_duplicate dict matching what service.upload_file returns."""
    return {
        "id": source_id,
        "skipped_duplicate": True,
        "existing_status": existing_status,
        "filename": filename,
        "status": existing_status,
    }


def _make_console() -> tuple[Console, StringIO]:
    """Return a (console, buffer) pair for output capture."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False)
    return console, buf


# ---------------------------------------------------------------------------
# Unit tests for _render_duplicate_skip (isolated helper)
# ---------------------------------------------------------------------------


class TestRenderDuplicateSkip:
    """Unit tests for the static rendering helper — no service needed."""

    def test_error_status_suggests_delete(self) -> None:
        """Error-state sibling: output contains 'delete' and the source ID."""
        console, buf = _make_console()
        SourcePipeline._render_duplicate_skip(
            _make_skip_info(source_id="if_err123", existing_status="error"),
            console,
        )
        output = buf.getvalue()
        assert "if_err123" in output
        assert "delete" in output.lower()
        assert "chaoscypher source delete if_err123" in output

    def test_committed_status_shows_dim_info(self) -> None:
        """Already-committed sibling: dim informational message, no delete hint."""
        console, buf = _make_console()
        SourcePipeline._render_duplicate_skip(
            _make_skip_info(source_id="if_ok456", existing_status="committed"),
            console,
        )
        output = buf.getvalue()
        assert "if_ok456" in output
        assert "committed" in output
        # Should NOT suggest deleting a healthy source
        assert "delete" not in output.lower()

    def test_filename_appears_in_output(self) -> None:
        """The filename is included in the skip message."""
        console, buf = _make_console()
        SourcePipeline._render_duplicate_skip(
            _make_skip_info(filename="my_report.pdf", existing_status="indexed"),
            console,
        )
        output = buf.getvalue()
        assert "my_report.pdf" in output


# ---------------------------------------------------------------------------
# Integration tests through the full pipeline (mocked service)
# ---------------------------------------------------------------------------


class TestPipelineSkipDuplicates:
    """Pipeline-level tests: service returns skipped_duplicate, pipeline handles it."""

    def _make_pipeline(self, mock_context: MagicMock) -> tuple[SourcePipeline, Console, StringIO]:
        from chaoscypher_cli.sources.service import CLISourceProcessingService

        service = CLISourceProcessingService(mock_context)
        console, buf = _make_console()
        pipeline = SourcePipeline(service, console)
        return pipeline, console, buf

    def test_errored_duplicate_result_is_success(
        self, mock_cli_context: MagicMock, temp_dir: Any
    ) -> None:
        """Pipeline returns success=True and status=skipped_duplicate for errored sibling."""
        pipeline, _, _ = self._make_pipeline(mock_cli_context)
        skip_info = _make_skip_info(existing_status="error")

        with patch.object(
            pipeline.service,
            "upload_file",
            return_value=skip_info,
        ):
            sample = temp_dir / "doc.txt"
            sample.write_text("hello world")
            result = pipeline.run(
                file_path=sample,
                skip_duplicates=True,
                quiet=True,
            )

        assert result.success is True
        assert result.status == "skipped_duplicate"
        assert result.file_id == skip_info["id"]
        assert "upload" in result.stages_skipped

    def test_errored_duplicate_output_contains_delete_hint(
        self, mock_cli_context: MagicMock, temp_dir: Any
    ) -> None:
        """When existing source is errored, output suggests 'chaoscypher source delete <id>'."""
        pipeline, _, buf = self._make_pipeline(mock_cli_context)
        skip_info = _make_skip_info(source_id="if_errored99", existing_status="error")

        with patch.object(
            pipeline.service,
            "upload_file",
            return_value=skip_info,
        ):
            sample = temp_dir / "doc.txt"
            sample.write_text("hello world")
            pipeline.run(
                file_path=sample,
                skip_duplicates=True,
                quiet=True,
            )

        output = buf.getvalue()
        assert "chaoscypher source delete if_errored99" in output

    def test_committed_duplicate_no_delete_hint(
        self, mock_cli_context: MagicMock, temp_dir: Any
    ) -> None:
        """When existing source is committed, output is informational (no delete hint)."""
        pipeline, _, buf = self._make_pipeline(mock_cli_context)
        skip_info = _make_skip_info(source_id="if_done99", existing_status="committed")

        with patch.object(
            pipeline.service,
            "upload_file",
            return_value=skip_info,
        ):
            sample = temp_dir / "doc.txt"
            sample.write_text("hello world")
            pipeline.run(
                file_path=sample,
                skip_duplicates=True,
                quiet=True,
            )

        output = buf.getvalue()
        assert "if_done99" in output
        assert "delete" not in output.lower()

    def test_no_downstream_stages_run_on_skip(
        self, mock_cli_context: MagicMock, temp_dir: Any
    ) -> None:
        """When duplicate is detected, indexing/extraction/commit must NOT run."""
        pipeline, _, _ = self._make_pipeline(mock_cli_context)
        skip_info = _make_skip_info(existing_status="committed")

        with patch.object(
            pipeline.service,
            "upload_file",
            return_value=skip_info,
        ):
            with patch.object(pipeline.service, "index_file") as mock_index:
                with patch.object(pipeline.service, "extract_entities") as mock_extract:
                    with patch.object(pipeline.service, "commit_to_graph") as mock_commit:
                        sample = temp_dir / "doc.txt"
                        sample.write_text("hello world")
                        pipeline.run(
                            file_path=sample,
                            skip_duplicates=True,
                            quiet=True,
                        )

        mock_index.assert_not_called()
        mock_extract.assert_not_called()
        mock_commit.assert_not_called()

    def test_without_skip_duplicates_flag_normal_upload_runs(
        self, mock_cli_context: MagicMock, temp_dir: Any
    ) -> None:
        """Without --skip-duplicates, upload_file is called normally (returning str)."""
        pipeline, _, _ = self._make_pipeline(mock_cli_context)

        file_id_returned = "if_newfile123456"
        with patch.object(
            pipeline.service,
            "upload_file",
            return_value=file_id_returned,
        ) as mock_upload:
            with patch.object(
                pipeline.service,
                "index_file",
                return_value={"chunks_count": 3, "tokens_count": 100, "failed_embeddings": 0},
            ):
                sample = temp_dir / "doc.txt"
                sample.write_text("hello world")
                result = pipeline.run(
                    file_path=sample,
                    skip_duplicates=False,
                    skip_extract=True,
                    skip_commit=True,
                    quiet=True,
                )

        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args.kwargs
        assert call_kwargs.get("skip_duplicates") is False
        assert result.status != "skipped_duplicate"


# ---------------------------------------------------------------------------
# Click command integration (via CliRunner)
# ---------------------------------------------------------------------------


class TestAddCommandSkipDuplicates:
    """End-to-end Click command tests for --skip-duplicates.

    The ``add`` command uses deferred imports (inside the function body), so
    patches must target the modules where the names are defined, not the
    ``add`` module itself.
    """

    def test_flag_accepted_by_cli(self, temp_dir: Any) -> None:
        """--skip-duplicates flag is accepted without error."""
        from click.testing import CliRunner

        from chaoscypher_cli.commands.source.add import add as add_command

        sample = temp_dir / "doc.txt"
        sample.write_text("hello world")
        cli_runner = CliRunner()

        # Patch everything so no real I/O happens.
        # get_context / CLISourceProcessingService / SourcePipeline are imported
        # inside the add() body, so we patch at the defining module paths.
        fake_result = PipelineResult(
            file_id="if_existingabc",
            filename="doc.txt",
            success=True,
            status="skipped_duplicate",
            stages_skipped=["upload"],
        )

        with patch("chaoscypher_cli.context.get_context") as mock_ctx_fn:
            mock_ctx = MagicMock()
            mock_ctx.settings = MagicMock()
            mock_ctx.settings.paths.data_dir = str(temp_dir)
            mock_ctx_fn.return_value = mock_ctx

            with patch("chaoscypher_cli.sources.CLISourceProcessingService"):
                with patch("chaoscypher_cli.sources.SourcePipeline") as mock_pl_cls:
                    mock_pl = MagicMock()
                    mock_pl.run.return_value = fake_result
                    mock_pl_cls.return_value = mock_pl

                    with patch(
                        "chaoscypher_core.services.sources.loaders.factory.get_loader_registry"
                    ) as mock_registry_fn:
                        mock_reg = MagicMock()
                        mock_reg.list_supported_extensions.return_value = [".txt"]
                        mock_registry_fn.return_value = mock_reg

                        with patch(
                            "chaoscypher_cli.utils.llm_check.check_llm_or_skip",
                            return_value=(True, False),
                        ):
                            result = cli_runner.invoke(
                                add_command,
                                [str(sample), "--skip-duplicates"],
                            )

        assert result.exit_code == 0, result.output

    def test_skip_duplicates_forwarded_to_pipeline_run(self, temp_dir: Any) -> None:
        """Verify pipeline.run is called with skip_duplicates=True when flag is set."""
        from click.testing import CliRunner

        from chaoscypher_cli.commands.source.add import add as add_command

        sample = temp_dir / "report.txt"
        sample.write_text("some content")
        cli_runner = CliRunner()

        fake_result = PipelineResult(
            file_id="if_abc",
            filename="report.txt",
            success=True,
            status="completed",
        )

        captured_kwargs: dict[str, Any] = {}

        def capture_run(**kwargs: Any) -> PipelineResult:
            captured_kwargs.update(kwargs)
            return fake_result

        with patch("chaoscypher_cli.context.get_context") as mock_ctx_fn:
            mock_ctx = MagicMock()
            mock_ctx.settings = MagicMock()
            mock_ctx.settings.paths.data_dir = str(temp_dir)
            mock_ctx_fn.return_value = mock_ctx

            with patch("chaoscypher_cli.sources.CLISourceProcessingService"):
                with patch("chaoscypher_cli.sources.SourcePipeline") as mock_pl_cls:
                    mock_pl = MagicMock()
                    mock_pl.run.side_effect = capture_run
                    mock_pl_cls.return_value = mock_pl

                    with patch(
                        "chaoscypher_core.services.sources.loaders.factory.get_loader_registry"
                    ) as mock_registry_fn:
                        mock_reg = MagicMock()
                        mock_reg.list_supported_extensions.return_value = [".txt"]
                        mock_registry_fn.return_value = mock_reg

                        with patch(
                            "chaoscypher_cli.utils.llm_check.check_llm_or_skip",
                            return_value=(True, False),
                        ):
                            cli_runner.invoke(
                                add_command,
                                [str(sample), "--skip-duplicates"],
                            )

        assert captured_kwargs.get("skip_duplicates") is True
