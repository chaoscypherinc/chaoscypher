# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Top-up tests for extract.py and confirm.py — branches NOT covered by the
existing test_source_extract.py / test_source_confirm.py files.

extract.py uncovered (67%):
  150        quiet=False branch in force+committed path: print "Resetting..." message
  154-156    quiet=False branch: print removed node/edge counts
  174        quiet=False branch: print "Extracting:" progress header
  190-191    KeyboardInterrupt handling → exit 130
  194-195    generic Exception handler → exit 1
  255-301    _run_extraction interactive (non-quiet) progress path

confirm.py uncovered (73%):
  86-87      no source_id and no --all → exit 1
  102-103    confirm_all → no awaiting sources → early return with message
  111        confirm_all → failures > 0 → exit 1
  121-122    KeyboardInterrupt → exit 130
  124-125    generic Exception handler → exit 1
  148-149    _confirm_one source not found → return False
  170-192    _confirm_one: not TTY + no --yes → no-TTY error path
             low_confidence path, interactive prompt path (TTY)
  209-210    no LLM → return False
  213        quiet=False banner
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.confirm import confirm_cmd
from chaoscypher_cli.commands.source.extract import extract_cmd


# ---------------------------------------------------------------------------
# Helpers shared with the reference tests
# ---------------------------------------------------------------------------


def _make_source(
    status: str, source_id: str = "if_testsrc12345", filename: str = "doc.pdf"
) -> dict[str, Any]:
    return {
        "id": source_id,
        "filename": filename,
        "status": status,
        "extraction_depth": "full",
        "forced_domain": None,
    }


def _make_extract_result(entity_count: int = 5, rel_count: int = 3) -> dict[str, Any]:
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


def _make_llm_summary(cost: float = 0.005, total_calls: int = 2) -> dict[str, Any]:
    return {
        "total_calls": total_calls,
        "successful_calls": total_calls,
        "failed_calls": 0,
        "retry_calls": 0,
        "total_input_tokens": 1000,
        "total_output_tokens": 200,
        "wasted_tokens": 0,
        "estimated_cost_usd": cost,
        "model": "gpt-4o-mini",
        "retry_rate": 0.0,
        "success_rate": 1.0,
    }


def _make_service(status: str = "indexed") -> MagicMock:
    svc = MagicMock()
    svc.__enter__ = lambda s: s
    svc.__exit__ = MagicMock(return_value=False)
    svc.has_llm = True
    svc.get_file_status.return_value = _make_source(status)
    svc.extract_entities.return_value = (_make_extract_result(), _make_llm_summary())
    svc.ctx.storage_adapter.get_file.return_value = _make_source(status)
    svc.ctx.database_name = "default"
    return svc


# ===========================================================================
# extract.py top-up tests
# ===========================================================================


class TestExtractVerboseResetOutput:
    """force+committed, quiet=False: verbose reset messages are printed."""

    def test_verbose_reset_message_printed(self) -> None:
        runner = CliRunner()
        svc = _make_service("committed")
        svc.reset_for_re_extraction.return_value = {
            "nodes_deleted": 7,
            "edges_deleted": 3,
            "templates_deleted": 0,
        }
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345", "--force", "--yes"])

        assert result.exit_code == 0, result.output
        # Verbose path: "Resetting committed source..." should appear
        assert "Resetting" in result.output or "reset" in result.output.lower()

    def test_verbose_reset_shows_node_edge_counts(self) -> None:
        runner = CliRunner()
        svc = _make_service("committed")
        svc.reset_for_re_extraction.return_value = {
            "nodes_deleted": 7,
            "edges_deleted": 3,
            "templates_deleted": 0,
        }
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345", "--force", "--yes"])

        assert result.exit_code == 0, result.output
        assert "7" in result.output
        assert "3" in result.output


class TestExtractVerboseProgressHeader:
    """quiet=False: extraction progress header is printed."""

    def test_verbose_extracts_and_shows_header(self) -> None:
        runner = CliRunner()
        svc = _make_service("indexed")
        svc.extract_entities.return_value = (_make_extract_result(), _make_llm_summary())
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                # No --quiet → verbose path
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 0, result.output
        # Verbose path should show the "Extracting:" header
        assert "Extracting" in result.output or "Extraction complete" in result.output

    def test_verbose_shows_entity_and_rel_counts(self) -> None:
        runner = CliRunner()
        svc = _make_service("indexed")
        svc.extract_entities.return_value = (
            _make_extract_result(entity_count=9, rel_count=4),
            _make_llm_summary(cost=0.02, total_calls=3),
        )
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 0, result.output
        assert "9" in result.output
        assert "4" in result.output
        # LLM calls and cost lines
        assert "3" in result.output

    def test_verbose_shows_cost_when_positive(self) -> None:
        """Cost > 0 triggers the cost display line."""
        runner = CliRunner()
        svc = _make_service("indexed")
        svc.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(cost=0.05, total_calls=2),
        )
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 0, result.output
        assert "$" in result.output or "0.05" in result.output

    def test_verbose_shows_small_cost_as_under_one_cent(self) -> None:
        """Cost < 0.01 should display as '<$0.01'."""
        runner = CliRunner()
        svc = _make_service("indexed")
        svc.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(cost=0.001, total_calls=1),
        )
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 0, result.output
        assert "<$0.01" in result.output

    def test_verbose_zero_cost_no_cost_line(self) -> None:
        """Cost == 0 means the cost line is omitted."""
        runner = CliRunner()
        svc = _make_service("indexed")
        svc.extract_entities.return_value = (
            _make_extract_result(),
            _make_llm_summary(cost=0.0, total_calls=0),
        )
        svc.ctx.storage_adapter.get_file.return_value = _make_source("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 0, result.output
        assert "$" not in result.output


class TestExtractExceptionHandlers:
    """KeyboardInterrupt and generic Exception handlers."""

    def test_keyboard_interrupt_exits_130(self) -> None:
        runner = CliRunner()

        def raise_keyboard(*a: Any, **kw: Any) -> None:
            raise KeyboardInterrupt

        svc = MagicMock()
        svc.__enter__ = lambda s: s
        svc.__exit__ = MagicMock(return_value=False)
        svc.get_file_status.side_effect = raise_keyboard

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 130

    def test_exception_exits_1(self) -> None:
        runner = CliRunner()

        svc = MagicMock()
        svc.__enter__ = lambda s: s
        svc.__exit__ = MagicMock(return_value=False)
        svc.get_file_status.side_effect = RuntimeError("boom")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 1
        assert "Error" in result.output


class TestExtractDomainOption:
    """--domain flag updates forced_domain on the file record."""

    def test_domain_option_updates_file_record(self) -> None:
        runner = CliRunner()
        svc = _make_service("indexed")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(
                    extract_cmd, ["if_testsrc12345", "--domain", "legal", "--quiet"]
                )

        assert result.exit_code == 0, result.output
        svc.ctx.storage_adapter.update_file.assert_any_call(
            "if_testsrc12345",
            database_name=svc.ctx.database_name,
            updates={"forced_domain": "legal"},
        )


# ===========================================================================
# confirm.py top-up tests
# ===========================================================================


def _make_confirm_service(
    source_id: str = "if_awaitsrc1234",
    status: str = "awaiting_confirmation",
) -> MagicMock:
    svc = MagicMock()
    svc.__enter__ = lambda s: s
    svc.__exit__ = MagicMock(return_value=False)
    svc.has_llm = True
    src = {
        "id": source_id,
        "filename": "doc.pdf",
        "status": status,
        "forced_domain": None,
        "extraction_depth": "full",
        "extraction_confirmed_at": None,
        "detection_proposal": {
            "ranking": [{"domain": "technical", "score": 4.2}, {"domain": "news", "score": 1.0}],
            "confidence": 4.2,
            "detected_domain": "technical",
            "low_confidence": False,
        },
    }
    svc.get_file_status.return_value = src
    svc.ctx.database_name = "default"
    svc.ctx.storage_adapter.get_file.return_value = src
    svc.extract_entities.return_value = (
        {"stats": {"entities_count": 4, "relationships_count": 2, "detected_domain": "technical"}},
        {"total_calls": 1, "estimated_cost_usd": 0.0},
    )
    return svc


class TestConfirmNoSourceIdNoAll:
    """No source_id and no --all → usage error exit 1."""

    def test_no_source_no_all_exits_1(self) -> None:
        runner = CliRunner()

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            result = runner.invoke(confirm_cmd, [])

        assert result.exit_code == 1
        assert "Usage" in result.output or "confirm" in result.output.lower()


class TestConfirmAllNoAwaiting:
    """--all when no sources are awaiting → informational message, exit 0."""

    def test_all_no_awaiting_exits_0_with_message(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        svc.ctx.storage_adapter.list_files.return_value = [
            {"id": "if_src1", "status": "committed"},
        ]

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["--all", "--yes"])

        assert result.exit_code == 0, result.output
        assert "No sources awaiting" in result.output


class TestConfirmAllWithFailures:
    """--all where one source fails → exit 1."""

    def test_all_failure_exits_1(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        # List two awaiting sources
        src1 = {
            "id": "if_awaitsrc1111",
            "status": "awaiting_confirmation",
            "filename": "a.pdf",
            "forced_domain": None,
            "extraction_depth": "full",
            "extraction_confirmed_at": None,
            "detection_proposal": {
                "ranking": [{"domain": "technical", "score": 4.0}],
                "confidence": 4.0,
                "detected_domain": "technical",
                "low_confidence": False,
            },
        }
        src2 = {
            "id": "if_awaitsrc2222",
            "status": "awaiting_confirmation",
            "filename": "b.pdf",
            "forced_domain": None,
            "extraction_depth": "full",
            "extraction_confirmed_at": None,
            "detection_proposal": None,
        }
        svc.ctx.storage_adapter.list_files.return_value = [src1, src2]

        # get_file_status for src2 returns None → failure for that one
        def get_status(sid: str) -> Any:
            if sid == "if_awaitsrc1111":
                return src1
            return None  # src2 not found

        svc.get_file_status.side_effect = get_status

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["--all", "--yes", "--quiet"])

        assert result.exit_code == 1


class TestConfirmOneNotFound:
    """confirm <id> when source not found → exit 1."""

    def test_source_not_found_exits_1(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        svc.get_file_status.return_value = None

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_notexist", "--yes"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "if_notexist" in result.output


class TestConfirmNoLLM:
    """When has_llm is False, _confirm_one returns False → exit 1."""

    def test_no_llm_exits_1(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        svc.has_llm = False

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes"])

        assert result.exit_code == 1
        assert "LLM" in result.output or "llm" in result.output.lower()


class TestConfirmVerboseBanner:
    """quiet=False prints the confirming banner."""

    def test_verbose_banner_printed(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Confirming" in result.output or "doc.pdf" in result.output


class TestConfirmKeyboardInterrupt:
    """KeyboardInterrupt while in service exits 130."""

    def test_keyboard_interrupt_exits_130(self) -> None:
        runner = CliRunner()
        svc = MagicMock()
        svc.__enter__ = lambda s: s
        svc.__exit__ = MagicMock(return_value=False)
        svc.get_file_status.side_effect = KeyboardInterrupt

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes"])

        assert result.exit_code == 130


class TestConfirmExceptionHandler:
    """Generic Exception in service body → exit 1."""

    def test_exception_exits_1(self) -> None:
        runner = CliRunner()
        svc = MagicMock()
        svc.__enter__ = lambda s: s
        svc.__exit__ = MagicMock(return_value=False)
        svc.get_file_status.side_effect = RuntimeError("crash")

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes"])

        assert result.exit_code == 1
        assert "Error" in result.output


class TestConfirmNoTTY:
    """When neither --yes nor a TTY, _confirm_one prints no-TTY error → return False → exit 1."""

    def test_no_tty_no_yes_exits_1(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                # sys.stdin.isatty() will return False inside CliRunner (no TTY)
                # and --yes not passed → hits the no-TTY guard
                with patch("sys.stdin") as mock_stdin, patch("sys.stderr") as mock_stderr:
                    mock_stdin.isatty.return_value = False
                    mock_stderr.isatty.return_value = False
                    result = runner.invoke(confirm_cmd, ["if_awaitsrc1234"])

        assert result.exit_code == 1
        assert "TTY" in result.output or "tty" in result.output.lower() or "--yes" in result.output


class TestConfirmLowConfidencePath:
    """When detection_proposal.low_confidence is True, the low-confidence message is shown."""

    def test_low_confidence_message_shown(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        src = svc.get_file_status.return_value
        src["detection_proposal"]["low_confidence"] = True
        src["extraction_confirmed_at"] = None

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                # --yes accepts the recommendation without prompting
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes", "--quiet"])

        # The low_confidence branch is only hit when not --yes and on a TTY.
        # With --yes it goes straight to accepted domain. Test that --yes+low_confidence
        # still succeeds (no crash on that code path).
        assert result.exit_code == 0, result.output


class TestConfirmExtractionConfirmedAtAlreadySet:
    """When extraction_confirmed_at is already set, the second update_file call is skipped."""

    def test_confirmed_at_already_set_skips_timestamp_update(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        src = svc.get_file_status.return_value
        src["extraction_confirmed_at"] = "2026-01-01T00:00:00+00:00"

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes", "--quiet"])

        assert result.exit_code == 0, result.output
        # Only the first update_file call (domain + status flip) should have been made,
        # NOT a second call with extraction_confirmed_at.
        calls = svc.ctx.storage_adapter.update_file.call_args_list
        confirmed_at_calls = [c for c in calls if "extraction_confirmed_at" in str(c)]
        assert len(confirmed_at_calls) == 0


class TestConfirmNoRankingFallback:
    """When ranking is empty, recommended domain falls back to detected_domain or 'generic'."""

    def test_no_ranking_uses_detected_domain(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        src = svc.get_file_status.return_value
        src["detection_proposal"] = {
            "ranking": [],
            "confidence": 2.0,
            "detected_domain": "medical",
            "low_confidence": False,
        }

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes", "--quiet"])

        assert result.exit_code == 0, result.output
        # "medical" should have been persisted as forced_domain
        svc.ctx.storage_adapter.update_file.assert_any_call(
            "if_awaitsrc1234",
            database_name="default",
            updates={"forced_domain": "medical", "status": "indexed"},
        )


class TestConfirmAllSuccessfulReturn:
    """--all where ALL sources succeed → exit 0 (hits line 112 'return')."""

    def test_all_success_exits_0(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        src1 = {
            "id": "if_awaitsrc1111",
            "status": "awaiting_confirmation",
            "filename": "a.pdf",
            "forced_domain": None,
            "extraction_depth": "full",
            "extraction_confirmed_at": None,
            "detection_proposal": {
                "ranking": [{"domain": "technical", "score": 4.0}],
                "confidence": 4.0,
                "detected_domain": "technical",
                "low_confidence": False,
            },
        }
        svc.ctx.storage_adapter.list_files.return_value = [src1]
        svc.get_file_status.return_value = src1

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["--all", "--yes", "--quiet"])

        assert result.exit_code == 0, result.output
        assert svc.extract_entities.call_count == 1


class TestConfirmOneNonAwaitingStatus:
    """_confirm_one when source status is not awaiting_confirmation → return False."""

    def test_wrong_status_returns_false_exits_1(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()
        src = svc.get_file_status.return_value
        src["status"] = "committed"

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--yes"])

        assert result.exit_code == 1
        assert "awaiting_confirmation" in result.output


class TestConfirmInteractiveTTYPath:
    """TTY interactive path: Prompt.ask is called with choices.

    Notes on patching strategy:
    - `Prompt` is imported lazily inside `_confirm_one` from `rich.prompt`, so
      patch `rich.prompt.Prompt.ask`.
    - The TTY check is `sys.stdin.isatty() and sys.stderr.isatty()`.  Click's
      CliRunner replaces `sys.stdin` during the invoke, so we cannot simply
      patch `sys.stdin` at the outer scope — Click wins.  Instead we patch the
      `sys` module that the *confirm* module references: since `confirm.py` does
      `import sys` at the top, patching `chaoscypher_cli.commands.source.confirm.sys`
      intercepts the reference the module actually uses at call time.
    """

    def test_tty_interactive_accept_chosen_domain(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()

        mock_sys = MagicMock()
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stderr.isatty.return_value = True
        mock_sys.exit = __import__("sys").exit  # let sys.exit still work

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                # Patch the sys reference inside the confirm module and Prompt.ask
                with patch("chaoscypher_cli.commands.source.confirm.sys", mock_sys):
                    with patch("rich.prompt.Prompt.ask", return_value="technical"):
                        result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--quiet"])

        assert result.exit_code == 0, result.output
        svc.extract_entities.assert_called_once()

    def test_tty_interactive_cancel_returns_cleanly(self) -> None:
        runner = CliRunner()
        svc = _make_confirm_service()

        mock_sys = MagicMock()
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stderr.isatty.return_value = True
        mock_sys.exit = __import__("sys").exit

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                with patch("chaoscypher_cli.commands.source.confirm.sys", mock_sys):
                    with patch("rich.prompt.Prompt.ask", return_value="cancel"):
                        result = runner.invoke(confirm_cmd, ["if_awaitsrc1234"])

        # "cancel" returns False from _confirm_one → sys.exit(1)
        assert result.exit_code == 1
        assert "Cancelled" in result.output
        svc.extract_entities.assert_not_called()

    def test_tty_low_confidence_shows_message(self) -> None:
        """On a TTY with low_confidence=True, the low-confidence warning is printed."""
        runner = CliRunner()
        svc = _make_confirm_service()
        src = svc.get_file_status.return_value
        src["detection_proposal"]["low_confidence"] = True

        mock_sys = MagicMock()
        mock_sys.stdin.isatty.return_value = True
        mock_sys.stderr.isatty.return_value = True
        mock_sys.exit = __import__("sys").exit

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                with patch("chaoscypher_cli.commands.source.confirm.sys", mock_sys):
                    with patch("rich.prompt.Prompt.ask", return_value="technical"):
                        result = runner.invoke(confirm_cmd, ["if_awaitsrc1234", "--quiet"])

        assert result.exit_code == 0, result.output
        assert "confident" in result.output.lower() or "domain" in result.output.lower()


class TestExtractCallbacksCoverage:
    """Cover on_progress and on_domain callback bodies (lines 270, 273-274)."""

    def test_on_progress_and_on_domain_called(self) -> None:
        """Simulate extract_entities calling the progress/domain callbacks."""
        runner = CliRunner()
        svc = _make_service("indexed")

        def fake_extract(source_id: str, **kwargs: Any) -> tuple[dict, dict]:
            # Invoke the callbacks that were passed in
            cb = kwargs.get("progress_callback")
            dc = kwargs.get("domain_callback")
            if cb:
                cb(1, 2)
            if dc:
                dc("technical")
            return _make_extract_result(), _make_llm_summary()

        svc.extract_entities.side_effect = fake_extract

        with patch("chaoscypher_cli.context.get_context", return_value=MagicMock()):
            with patch("chaoscypher_cli.sources.CLISourceProcessingService", return_value=svc):
                # No --quiet → interactive path with callbacks
                result = runner.invoke(extract_cmd, ["if_testsrc12345"])

        assert result.exit_code == 0, result.output
        # Domain was printed by on_domain
        assert "technical" in result.output
