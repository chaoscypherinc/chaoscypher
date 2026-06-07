# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioral coverage for ``chaoscypher source add``.

Drives the ``add`` command through Click's ``CliRunner`` (with a real
``tmp_path`` file and URL inputs) plus direct calls to the module's pure
helpers. Every dependency that would do real I/O / LLM / network work is
patched at the module where ``add`` looks it up at call time:

- ``get_context`` / ``CLISourceProcessingService`` / ``SourcePipeline`` are
  imported lazily inside the function body, so they are patched at their
  *defining* module paths (``chaoscypher_cli.context`` /
  ``chaoscypher_cli.sources``), not in the ``add`` namespace.
- ``get_loader_registry`` (used by ``_validate_file`` / ``_expand_inputs``)
  and ``check_llm_or_skip`` are likewise patched where defined.

Scenarios covered:
- Helpers: ``_is_file_id`` / ``_is_url`` boundary cases, ``_get_pending_files``
  filtering, ``_show_resume_picker`` (empty / pick / quit / invalid / bad-input),
  ``_validate_file`` (missing / unsupported / ok), ``_expand_inputs``
  (file / url / file-id / directory / empty-dir / missing-file), ``_result_to_dict``
  (with and without LLM metrics), ``_show_batch_summary``.
- Command: single file upload forwards all flag-derived settings to
  ``pipeline.run``; URL input; file-id resume; missing file-id; no-args usage;
  mixed file-id + file error; empty expansion; ``--quiet`` OK / FAILED /
  AWAITING output; ``--json`` output; multi-file batch summary; the
  ``check_llm_or_skip`` cancel and skip branches; resume picker -> awaiting
  source delegates to ``confirm``; exit-1 on failure; exception handlers.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from rich.console import Console

from chaoscypher_cli.commands.source.add import (
    _expand_inputs,
    _get_pending_files,
    _is_file_id,
    _is_url,
    _result_to_dict,
    _show_batch_summary,
    _show_resume_picker,
    _validate_file,
    add,
)
from chaoscypher_cli.sources.pipeline import PipelineResult


_ADD = "chaoscypher_cli.commands.source.add"
_FILE_ID = "if_abcdefgh1234"  # 15 chars -> passes _is_file_id()


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=100), buf


def _registry(extensions: list[str]) -> MagicMock:
    reg = MagicMock()
    reg.list_supported_extensions.return_value = extensions
    return reg


def _result(**overrides: Any) -> PipelineResult:
    base: dict[str, Any] = {
        "file_id": "if_result1234567",
        "filename": "doc.txt",
        "success": True,
        "status": "completed",
    }
    base.update(overrides)
    return PipelineResult(**base)


class _AddHarness:
    """Set up all patches for an ``add`` invocation and capture pipeline.run."""

    def __init__(
        self,
        results: PipelineResult | list[PipelineResult],
        *,
        extensions: list[str] | None = None,
        llm: tuple[bool, bool] = (True, False),
    ) -> None:
        self.results = results if isinstance(results, list) else [results]
        self.extensions = extensions if extensions is not None else [".txt", ".pdf", ".md"]
        self.llm = llm
        self.captured_runs: list[dict[str, Any]] = []
        self.ctx = MagicMock()
        self.service = MagicMock()
        self.service.__enter__ = lambda s: s
        self.service.__exit__ = MagicMock(return_value=False)
        self._patches: list[Any] = []
        self.pipeline = MagicMock()

    def __enter__(self) -> _AddHarness:
        result_iter = iter(self.results)

        def _run(**kwargs: Any) -> PipelineResult:
            self.captured_runs.append(kwargs)
            try:
                return next(result_iter)
            except StopIteration:  # pragma: no cover - defensive
                return self.results[-1]

        self.pipeline.run.side_effect = _run

        self._patches = [
            patch("chaoscypher_cli.context.get_context", return_value=self.ctx),
            patch(
                "chaoscypher_cli.sources.CLISourceProcessingService",
                return_value=self.service,
            ),
            patch("chaoscypher_cli.sources.SourcePipeline", return_value=self.pipeline),
            patch(
                "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
                return_value=_registry(self.extensions),
            ),
            patch(
                "chaoscypher_cli.utils.llm_check.check_llm_or_skip",
                return_value=self.llm,
            ),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        for p in reversed(self._patches):
            p.stop()


# ===========================================================================
# Pure helpers (no CliRunner needed)
# ===========================================================================


class TestIsFileId:
    def test_valid_file_id(self) -> None:
        assert _is_file_id("if_abcdefgh1234") is True

    def test_wrong_prefix(self) -> None:
        assert _is_file_id("xx_abcdefgh1234") is False

    def test_wrong_length(self) -> None:
        assert _is_file_id("if_short") is False


class TestIsUrl:
    @pytest.mark.parametrize("value", ["http://x.com", "https://x.com/a"])
    def test_urls(self, value: str) -> None:
        assert _is_url(value) is True

    def test_not_url(self) -> None:
        assert _is_url("/local/path.txt") is False


class TestGetPendingFiles:
    def test_filters_committed_and_failed(self) -> None:
        ctx = MagicMock()
        ctx.database_name = "default"
        ctx.storage_adapter.list_files.return_value = [
            {"id": "if_1", "status": "indexed"},
            {"id": "if_2", "status": "committed"},
            {"id": "if_3", "status": "failed"},
            {"id": "if_4", "status": "pending"},
        ]
        pending = _get_pending_files(ctx)
        ids = [f["id"] for f in pending]
        assert ids == ["if_1", "if_4"]


class TestShowResumePicker:
    def _ctx(self, pending: list[dict[str, Any]]) -> MagicMock:
        ctx = MagicMock()
        ctx.database_name = "default"
        all_files = [*pending, {"id": "if_done", "status": "committed"}]
        ctx.storage_adapter.list_files.return_value = all_files
        ctx.storage_adapter.list_chunks.return_value = [1, 2, 3]
        return ctx

    def test_no_pending_returns_none(self) -> None:
        ctx = self._ctx([])
        console, buf = _console()
        assert _show_resume_picker(ctx, console) is None
        assert "No pending files" in buf.getvalue()

    def test_pick_returns_selected_id(self) -> None:
        ctx = self._ctx(
            [
                {"id": "if_a", "status": "indexed", "filename": "a.pdf"},
                {"id": "if_b", "status": "pending", "filename": "b.pdf"},
            ]
        )
        console, _ = _console()
        with patch("rich.prompt.Prompt.ask", return_value="2"):
            assert _show_resume_picker(ctx, console) == "if_b"

    def test_quit_returns_none(self) -> None:
        ctx = self._ctx([{"id": "if_a", "status": "indexed", "filename": "a.pdf"}])
        console, _ = _console()
        with patch("rich.prompt.Prompt.ask", return_value="q"):
            assert _show_resume_picker(ctx, console) is None

    def test_invalid_index_returns_none(self) -> None:
        ctx = self._ctx([{"id": "if_a", "status": "indexed", "filename": "a.pdf"}])
        console, buf = _console()
        with patch("rich.prompt.Prompt.ask", return_value="99"):
            assert _show_resume_picker(ctx, console) is None
        assert "Invalid selection" in buf.getvalue()

    def test_non_numeric_returns_none(self) -> None:
        ctx = self._ctx([{"id": "if_a", "status": "indexed", "filename": "a.pdf"}])
        console, _ = _console()
        with patch("rich.prompt.Prompt.ask", return_value="not-a-number"):
            assert _show_resume_picker(ctx, console) is None


class TestValidateFile:
    def test_missing_file_exits_1(self, tmp_path: Any) -> None:
        console, buf = _console()
        missing = tmp_path / "nope.txt"
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt"]),
        ):
            with pytest.raises(SystemExit) as exc:
                _validate_file(missing, MagicMock(), console)
        assert exc.value.code == 1
        assert "File not found" in buf.getvalue()

    def test_unsupported_extension_exits_1(self, tmp_path: Any) -> None:
        console, buf = _console()
        f = tmp_path / "file.xyz"
        f.write_text("data")
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt", ".pdf"]),
        ):
            with pytest.raises(SystemExit) as exc:
                _validate_file(f, MagicMock(), console)
        assert exc.value.code == 1
        out = buf.getvalue()
        assert "Unsupported file type" in out
        assert ".xyz" in out

    def test_supported_file_passes(self, tmp_path: Any) -> None:
        console, _ = _console()
        f = tmp_path / "file.txt"
        f.write_text("data")
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt"]),
        ):
            # No exception means valid.
            _validate_file(f, MagicMock(), console)


class TestExpandInputs:
    def _expand(self, inputs: tuple[str, ...], extensions: list[str]) -> list[dict[str, Any]]:
        console, self._buf = _console()
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry(extensions),
        ):
            return _expand_inputs(inputs, MagicMock(), console)

    def test_file_id_input(self) -> None:
        items = self._expand((_FILE_ID,), [".txt"])
        assert items == [{"type": "file_id", "path": None, "url": None, "file_id": _FILE_ID}]

    def test_url_input(self) -> None:
        items = self._expand(("https://example.com/a",), [".txt"])
        assert items[0]["type"] == "url"
        assert items[0]["url"] == "https://example.com/a"

    def test_single_file_input(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        console, _ = _console()
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt"]),
        ):
            items = _expand_inputs((str(f),), MagicMock(), console)
        assert items[0]["type"] == "file"
        assert items[0]["path"].name == "doc.txt"

    def test_directory_expands_supported_files(self, tmp_path: Any) -> None:
        (tmp_path / "a.txt").write_text("1")
        (tmp_path / "b.pdf").write_text("2")
        (tmp_path / "c.skip").write_text("3")  # unsupported -> filtered out
        console, _ = _console()
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt", ".pdf"]),
        ):
            items = _expand_inputs((str(tmp_path),), MagicMock(), console)
        names = sorted(i["path"].name for i in items)
        assert names == ["a.txt", "b.pdf"]
        assert all(i["type"] == "file" for i in items)

    def test_directory_with_no_supported_files_warns(self, tmp_path: Any) -> None:
        (tmp_path / "c.skip").write_text("3")
        console, buf = _console()
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt"]),
        ):
            items = _expand_inputs((str(tmp_path),), MagicMock(), console)
        assert items == []
        assert "No supported files" in buf.getvalue()

    def test_missing_path_exits_1(self, tmp_path: Any) -> None:
        console, buf = _console()
        missing = str(tmp_path / "ghost.txt")
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=_registry([".txt"]),
        ):
            with pytest.raises(SystemExit) as exc:
                _expand_inputs((missing,), MagicMock(), console)
        assert exc.value.code == 1
        assert "File not found" in buf.getvalue()


class TestResultToDict:
    def test_without_llm_metrics(self) -> None:
        r = _result(file_id="if_x", filename="d.txt", entities_count=3, llm_total_calls=0)
        d = _result_to_dict(r)
        assert d["file_id"] == "if_x"
        assert d["entities_count"] == 3
        assert d["llm_metrics"] is None

    def test_with_llm_metrics(self) -> None:
        r = _result(
            llm_total_calls=2,
            llm_successful_calls=2,
            llm_total_input_tokens=100,
            llm_total_output_tokens=50,
            llm_model="gpt-4o-mini",
        )
        d = _result_to_dict(r)
        assert d["llm_metrics"]["total_calls"] == 2
        assert d["llm_metrics"]["total_tokens"] == 150
        assert d["llm_metrics"]["model"] == "gpt-4o-mini"


class TestShowBatchSummary:
    def test_mixed_success_and_failure(self) -> None:
        console, buf = _console()
        results = [
            _result(filename="ok.txt", success=True, duration_seconds=1.2),
            _result(filename="bad.txt", success=False, duration_seconds=0.5),
        ]
        _show_batch_summary(results, total_time=1.7, console=console)
        out = buf.getvalue()
        assert "Batch Complete" in out
        assert "1 succeeded" in out
        assert "1 failed" in out

    def test_all_success(self) -> None:
        console, buf = _console()
        results = [_result(filename="a.txt", success=True, duration_seconds=1.0)]
        _show_batch_summary(results, total_time=1.0, console=console)
        assert "1 succeeded" in buf.getvalue()


# ===========================================================================
# Command integration via CliRunner
# ===========================================================================


class TestAddSingleFile:
    def test_forwards_flag_derived_settings_to_pipeline(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        runner = CliRunner()

        with _AddHarness(_result()) as h:
            result = runner.invoke(
                add,
                [
                    str(f),
                    "--quick",
                    "--domain",
                    "legal",
                    "--no-vision",
                    "--no-content-filtering",
                    "--no-normalize",
                    "--filtering-mode",
                    "strict",
                    "--skip-embeddings",
                    "--skip-duplicates",
                    "--no-confirm",
                    "--verbose",
                ],
            )

        assert result.exit_code == 0, result.output
        assert len(h.captured_runs) == 1
        kw = h.captured_runs[0]
        assert kw["extraction_depth"] == "quick"
        assert kw["domain"] == "legal"
        assert kw["enable_vision"] is False
        assert kw["content_filtering"] is False
        assert kw["enable_normalization"] is False
        assert kw["filtering_mode"] == "strict"
        assert kw["skip_embeddings"] is True
        assert kw["skip_duplicates"] is True
        assert kw["no_confirm"] is True
        assert kw["verbose"] is True
        assert kw["file_path"].name == "doc.txt"
        assert kw["url"] is None
        assert kw["file_id"] is None

    def test_default_depth_is_full(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 0, result.output
        assert h.captured_runs[0]["extraction_depth"] == "full"
        assert h.captured_runs[0]["domain"] == "auto"
        assert h.captured_runs[0]["enable_vision"] is True

    def test_index_only_skips_llm_check_and_sets_flags(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        runner = CliRunner()
        # llm=(False, False) would cancel — but --index-only must bypass the
        # LLM gate entirely, so a cancel here would (wrongly) abort if reached.
        with _AddHarness(_result(), llm=(False, False)) as h:
            result = runner.invoke(add, [str(f), "--index-only"])
        assert result.exit_code == 0, result.output
        assert h.captured_runs[0]["index_only"] is True

    def test_skip_extract_bypasses_llm_check(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("hello")
        runner = CliRunner()
        with _AddHarness(_result(), llm=(False, False)) as h:
            result = runner.invoke(add, [str(f), "--skip-extract"])
        assert result.exit_code == 0, result.output
        assert h.captured_runs[0]["skip_extract"] is True


class TestAddUrl:
    def test_url_forwarded_as_url_arg(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result(filename="https://example.com/a")) as h:
            result = runner.invoke(add, ["https://example.com/a", "--quiet"])
        assert result.exit_code == 0, result.output
        kw = h.captured_runs[0]
        assert kw["url"] == "https://example.com/a"
        assert kw["file_path"] is None


class TestAddFileIdResume:
    def test_resume_by_file_id(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.ctx.storage_adapter.get_file.return_value = {
                "id": _FILE_ID,
                "filename": "old.pdf",
                "status": "indexed",
            }
            result = runner.invoke(add, [_FILE_ID])
        assert result.exit_code == 0, result.output
        assert "Resuming" in result.output
        assert h.captured_runs[0]["file_id"] == _FILE_ID
        assert h.captured_runs[0]["file_path"] is None

    def test_resume_by_file_id_quiet_suppresses_resuming_line(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.ctx.storage_adapter.get_file.return_value = {
                "id": _FILE_ID,
                "filename": "old.pdf",
                "status": "indexed",
            }
            result = runner.invoke(add, [_FILE_ID, "--quiet"])
        assert result.exit_code == 0, result.output
        assert "Resuming" not in result.output

    def test_missing_file_id_exits_1(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.ctx.storage_adapter.get_file.return_value = None
            result = runner.invoke(add, [_FILE_ID])
        assert result.exit_code == 1
        assert "File ID not found" in result.output


class TestAddNoArgs:
    def test_no_args_shows_usage_and_exits_0(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()):
            result = runner.invoke(add, [])
        assert result.exit_code == 0
        assert "Usage:" in result.output
        assert "--resume" in result.output


class TestAddValidationErrors:
    def test_file_ids_mixed_with_files_exits_1(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result()):
            result = runner.invoke(add, [_FILE_ID, str(f)])
        assert result.exit_code == 1
        assert "cannot be mixed" in result.output.lower()

    def test_empty_expansion_returns_no_files_message(self, tmp_path: Any) -> None:
        empty_dir = tmp_path / "emptydir"
        empty_dir.mkdir()
        runner = CliRunner()
        # Directory has only unsupported files -> _expand_inputs returns [].
        (empty_dir / "x.skip").write_text("z")
        with _AddHarness(_result(), extensions=[".txt"]):
            result = runner.invoke(add, [str(empty_dir)])
        assert result.exit_code == 0
        assert "No files to process" in result.output


class TestLLMCheckBranches:
    def test_llm_check_cancel_aborts(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result(), llm=(False, False)) as h:
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        # Pipeline never ran because we aborted before processing.
        assert h.captured_runs == []

    def test_llm_check_skip_sets_skip_extract(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result(), llm=(True, True)) as h:
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 0, result.output
        assert "without entity extraction" in result.output
        assert h.captured_runs[0]["skip_extract"] is True


class TestQuietOutput:
    def test_quiet_ok_line(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        ok = _result(file_id="if_ok1234567890", detected_domain="technical", success=True)
        with _AddHarness(ok):
            result = runner.invoke(add, [str(f), "--quiet"])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output
        assert "if_ok1234567890" in result.output
        assert "technical" in result.output

    def test_quiet_failed_line_exits_1(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        bad = _result(success=False, status="failed", error="boom")
        with _AddHarness(bad):
            result = runner.invoke(add, [str(f), "--quiet"])
        assert result.exit_code == 1
        assert "FAILED" in result.output
        assert "boom" in result.output

    def test_quiet_awaiting_line(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        parked = _result(
            file_id="if_park12345678",
            success=False,
            status="awaiting_confirmation",
            parked_for_confirmation=True,
            detected_domain="legal",
        )
        with _AddHarness(parked):
            result = runner.invoke(add, [str(f), "--quiet"])
        # Parked source is unsuccessful -> exit 1.
        assert result.exit_code == 1
        assert "AWAITING" in result.output
        assert "if_park12345678" in result.output
        assert "cc source confirm" in result.output


class TestJsonOutput:
    def test_json_single_file_emits_dict(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        r = _result(file_id="if_json12345678", entities_count=4)
        with _AddHarness(r):
            result = runner.invoke(add, [str(f), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert isinstance(payload, dict)
        assert payload["file_id"] == "if_json12345678"
        assert payload["entities_count"] == 4


class TestBatch:
    def test_multiple_files_show_batch_summary(self, tmp_path: Any) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("1")
        b.write_text("2")
        runner = CliRunner()
        results = [
            _result(file_id="if_a1234567890ab", filename="a.txt", duration_seconds=1.0),
            _result(file_id="if_b1234567890ab", filename="b.txt", duration_seconds=2.0),
        ]
        with _AddHarness(results) as h:
            result = runner.invoke(add, [str(a), str(b)])
        assert result.exit_code == 0, result.output
        assert len(h.captured_runs) == 2
        assert "Batch Complete" in result.output

    def test_batch_with_failure_exits_1(self, tmp_path: Any) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("1")
        b.write_text("2")
        runner = CliRunner()
        results = [
            _result(filename="a.txt", success=True, duration_seconds=1.0),
            _result(filename="b.txt", success=False, status="failed", duration_seconds=0.2),
        ]
        with _AddHarness(results):
            result = runner.invoke(add, [str(a), str(b)])
        assert result.exit_code == 1


class TestResumePickerCommand:
    def test_resume_picker_cancelled_returns_cleanly(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.ctx.storage_adapter.list_files.return_value = []
            result = runner.invoke(add, ["--resume"])
        assert result.exit_code == 0
        assert "No pending files" in result.output
        assert h.captured_runs == []

    def test_resume_picker_awaiting_delegates_to_confirm(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.ctx.storage_adapter.list_files.return_value = [
                {"id": _FILE_ID, "status": "indexed", "filename": "p.pdf"},
            ]
            h.ctx.storage_adapter.list_chunks.return_value = []
            h.ctx.storage_adapter.get_file.return_value = {
                "id": _FILE_ID,
                "filename": "p.pdf",
                "status": "awaiting_confirmation",
            }
            with patch("rich.prompt.Prompt.ask", return_value="1"):
                with patch(f"{_ADD}.click.get_current_context") as mock_get_cur:
                    mock_invoke = MagicMock()
                    mock_get_cur.return_value.invoke = mock_invoke
                    result = runner.invoke(add, ["--resume"])
        assert result.exit_code == 0, result.output
        # Delegated to the confirm command rather than running the pipeline.
        mock_invoke.assert_called_once()
        delegated_kwargs = mock_invoke.call_args.kwargs
        assert delegated_kwargs["source_id"] == _FILE_ID
        assert h.captured_runs == []

    def test_resume_picker_normal_source_runs_pipeline(self) -> None:
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.ctx.storage_adapter.list_files.return_value = [
                {"id": _FILE_ID, "status": "indexed", "filename": "p.pdf"},
            ]
            h.ctx.storage_adapter.list_chunks.return_value = []
            h.ctx.storage_adapter.get_file.return_value = {
                "id": _FILE_ID,
                "filename": "p.pdf",
                "status": "indexed",
            }
            with patch("rich.prompt.Prompt.ask", return_value="1"):
                result = runner.invoke(add, ["--resume"])
        assert result.exit_code == 0, result.output
        assert h.captured_runs[0]["file_id"] == _FILE_ID


class TestExceptionHandlers:
    def test_chaoscypher_exception_exits_1(self, tmp_path: Any) -> None:
        from chaoscypher_core.exceptions import ChaosCypherException

        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.pipeline.run.side_effect = ChaosCypherException("kaboom")
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 1
        assert "kaboom" in result.output

    def test_file_not_found_error_exits_1(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.pipeline.run.side_effect = FileNotFoundError("missing.txt")
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_permission_error_exits_1(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.pipeline.run.side_effect = PermissionError("denied")
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 1
        assert "Permission denied" in result.output

    def test_generic_exception_exits_1(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.pipeline.run.side_effect = RuntimeError("unexpected")
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 1
        assert "unexpected" in result.output

    def test_keyboard_interrupt_exits_130(self, tmp_path: Any) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("x")
        runner = CliRunner()
        with _AddHarness(_result()) as h:
            h.pipeline.run.side_effect = KeyboardInterrupt()
            result = runner.invoke(add, [str(f)])
        assert result.exit_code == 130
        assert "Cancelled" in result.output


class TestCommandRegistration:
    def test_add_name(self) -> None:
        assert add.name == "add"

    def test_add_has_expected_flags(self) -> None:
        params = {p.name for p in add.params}
        for flag in (
            "resume",
            "index_only",
            "skip_extract",
            "quick",
            "domain",
            "filtering_mode",
            "quiet",
            "output_json",
            "vision",
            "content_filtering",
            "skip_duplicates",
            "no_confirm",
        ):
            assert flag in params, flag
